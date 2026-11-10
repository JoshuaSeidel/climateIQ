"""Thermostat guard on device actions, notification target allow-list, cover hysteresis."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.api.main as m
import backend.api.routes.devices as dev
from backend.api.main import app
from backend.api.middleware import RateLimitMiddleware
from backend.integrations.ha_client import HAClient
from backend.models.database import (
    Device,
    Sensor,
    SensorReading,
    Zone,
    close_db,
    get_session_maker,
    init_db,
)
from backend.models.enums import ControlMethod, DeviceType
from backend.services.notification_service import NotificationService
from backend.tests.scenario.harness import House


def _reset_rate_limit() -> None:
    node = getattr(app, "middleware_stack", None)
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            node._requests.clear()
            return
        node = getattr(node, "app", None)


async def test_device_action_guard_covers_case_and_comma_lists(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m.app_state, "zone_manager", None)
    await close_db()
    await init_db()
    _reset_rate_limit()
    zid = await house.add_zone("Bedroom", 64)
    sent: list[Any] = []

    async def fake_dispatch(device: Any, payload: Any) -> dict[str, Any]:
        sent.append(payload.parameters)
        return {"success": True}

    monkeypatch.setattr(dev, "_dispatch_action", fake_dispatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/devices",
            json={
                "zone_id": str(zid),
                "name": "Fan",
                "type": "fan",
                "ha_entity_id": "fan.bedroom",
                "control_method": "ha_service_call",
            },
        )
        assert r.status_code == 201, r.text
        fan_id = r.json()["id"]
        for entity in (
            "climate.thermostat",
            "CLIMATE.Thermostat",
            "fan.bedroom,climate.thermostat",
            "climate.thermostat, fan.bedroom",
        ):
            r = await c.post(
                f"/api/v1/devices/{fan_id}/action",
                json={"action_type": "turn_off", "parameters": {"entity_id": entity}},
            )
            assert r.status_code == 409, (entity, r.status_code)
        # A device registered under the thermostat's id in another case is refused too.
        r = await c.post(
            "/api/v1/devices",
            json={
                "zone_id": str(zid),
                "name": "T2",
                "type": "other",
                "ha_entity_id": "Climate.Thermostat",
                "control_method": "ha_service_call",
            },
        )
        other = r.json()["id"]
        r = await c.post(
            f"/api/v1/devices/{other}/action",
            json={"action_type": "set_mode", "parameters": {"mode": "off"}},
        )
        assert r.status_code == 409
        assert sent == [], "nothing may reach the dispatcher for the managed thermostat"
        # A normal single-device action still works.
        r = await c.post(f"/api/v1/devices/{fan_id}/action", json={"action_type": "turn_off"})
        assert r.status_code == 200, r.text
        assert len(sent) == 1


async def test_notification_target_cannot_reach_other_services() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json=[])

    ha = HAClient(url="http://ha:8123", token="t")  # noqa: S106
    ha._client = httpx.AsyncClient(
        base_url="http://ha:8123", transport=httpx.MockTransport(handler)
    )
    ha._connected = True
    service = NotificationService(ha)
    # Injection attempts are NOT sent at all (and never broadcast).
    await service.send_ha_notification("t1", "m", target="mobile_app_x/../../homeassistant/restart")
    await service.send_ha_notification("t2", "m", target="x.y")
    assert seen == []
    # The usual spellings of a real target are accepted.
    for i, target in enumerate(
        ("mobile_app_josh", "notify.mobile_app_josh", "Mobile_App_Josh", " mobile_app_josh ")
    ):
        await service.send_ha_notification(f"ok{i}", "m", target=target)
    assert seen == ["/api/services/notify/mobile_app_josh"] * 4, seen
    # No target at all keeps using the default notify service.
    await service.send_ha_notification("default", "m")
    assert seen[-1] == "/api/services/notify/notify"


def test_managed_entity_matching_handles_case_and_comma_lists() -> None:
    managed = {"climate.thermostat"}
    assert m._is_managed_entity("climate.thermostat", managed)
    assert m._is_managed_entity("CLIMATE.Thermostat", managed)
    assert m._is_managed_entity("fan.x, climate.thermostat", managed)
    assert not m._is_managed_entity("fan.bedroom", managed)
    assert not m._is_managed_entity("climate.mini_split", managed)


async def test_cover_does_not_flap_when_lux_max_is_below_the_default_open_level(
    house: House,
) -> None:
    zid = await house.add_zone("Den", 70)
    async with get_session_maker()() as db:
        zone = (await db.execute(select(Zone).where(Zone.id == zid))).scalar_one()
        zone.comfort_preferences = {"lux_max": 150}  # all the Zones page sends
        sensor = (await db.execute(select(Sensor).where(Sensor.zone_id == zid))).scalars().first()
        assert sensor is not None
        db.add(
            Device(
                zone_id=zid,
                name="Blind",
                type=DeviceType.blind,
                ha_entity_id="cover.den",
                control_method=ControlMethod.ha_service_call,
            )
        )
        await db.commit()
        sensor_id = sensor.id
    m._cover_last_action.clear()
    house.ha.calls.clear()
    for _ in range(4):  # a constant 170 lx over four ticks
        async with get_session_maker()() as db:
            db.add(
                SensorReading(
                    sensor_id=sensor_id,
                    zone_id=zid,
                    recorded_at=datetime.now(UTC),
                    lux=170.0,
                    payload={},
                )
            )
            await db.commit()
        await m.execute_cover_automation()
    assert [c[1] for c in house.ha.calls if c[0] == "cover"] == ["close_cover"]


async def test_explicit_lux_open_is_honoured(house: House) -> None:
    """An explicit lux_open (450 with lux_max 500) is kept, not cut to 80% of lux_max (400)."""
    zid = await house.add_zone("Den", 70)
    async with get_session_maker()() as db:
        zone = (await db.execute(select(Zone).where(Zone.id == zid))).scalar_one()
        zone.comfort_preferences = {"lux_max": 500, "lux_open": 450}
        sensor = (await db.execute(select(Sensor).where(Sensor.zone_id == zid))).scalars().first()
        assert sensor is not None
        blind = Device(
            zone_id=zid,
            name="Blind",
            type=DeviceType.blind,
            ha_entity_id="cover.den",
            control_method=ControlMethod.ha_service_call,
        )
        db.add(blind)
        await db.commit()
        sensor_id, blind_id = sensor.id, blind.id
    m._cover_last_action.clear()
    m._cover_last_action[str(blind_id)] = "closed"
    house.ha.calls.clear()
    async with get_session_maker()() as db:
        db.add(
            SensorReading(
                sensor_id=sensor_id,
                zone_id=zid,
                recorded_at=datetime.now(UTC),
                lux=440.0,
                payload={},
            )
        )
        await db.commit()
    await m.execute_cover_automation()
    assert [c[1] for c in house.ha.calls if c[0] == "cover"] == ["open_cover"]
