"""Scenario regressions for review round 3 (docs/CONTROL_SPEC.md)."""

from __future__ import annotations

import asyncio
from typing import Any

import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


class _Notif:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_ha_notification(self, title: str, message: str, target: Any = None, **_k: Any) -> None:
        self.sent.append((title, message))


class _LLM:
    provider = "ollama"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def generate(self, **_kw: Any) -> dict[str, Any]:
        return {"content": self.answer}

    async def chat(self, **_kw: Any) -> dict[str, Any]:
        return {"content": self.answer}


def _use_llm(monkeypatch: Any, answer: str) -> _LLM:
    import backend.api.routes.chat as chat_mod

    llm = _LLM(answer)

    async def _get(_db: Any) -> _LLM:
        return llm

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)
    return llm


def _install_stepped_fan(house: House, eid: str, speeds: int = 3) -> list[int]:
    """A real HA fan with ``speeds`` steps: set_percentage snaps to a step."""
    import math

    house.ha.entities[eid] = EntityState(entity_id=eid, state="off", attributes={"percentage": 0})
    writes: list[int] = []
    orig = house.ha.call_service

    async def _cs(domain: str, service: str, data: Any = None, target: Any = None, **kw: Any) -> Any:
        payload = dict(data or {})
        if target:
            payload.update(target)
        if domain == "fan" and payload.get("entity_id") == eid:
            house.ha.calls.append((domain, service, payload))
            if service == "set_percentage":
                pct = int(payload["percentage"])
                writes.append(pct)
                step = math.ceil(pct * speeds / 100) if pct > 0 else 0
                actual = int(step * 100 / speeds)
            elif service == "turn_off":
                writes.append(0)
                actual = 0
            else:
                actual = 100
            house.ha.entities[eid] = EntityState(
                entity_id=eid, state="on" if actual else "off", attributes={"percentage": actual}
            )
            return []
        return await orig(domain, service, data, target, **kw)

    house.ha.call_service = _cs  # type: ignore[method-assign]
    return writes


async def test_follow_me_switch_tick_sends_only_switch_notification(house: House, monkeypatch: Any) -> None:
    """Spec §8 — a tick that switches heat↔cool sends only the switch notification."""
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.add_zone("Living Room", 76, comfort_preferences={"temp_min": 21, "temp_max": 23})
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=74, reading_f=75)

    async def _occ(_zid: Any, _db: Any) -> bool:
        return True

    monkeypatch.setattr(main_mod, "infer_zone_occupancy", _occ)
    house.at(*MON, 13, 0)
    await main_mod.execute_follow_me_mode()
    assert house.ha.mode == "cool"
    assert len(n.sent) == 1, n.sent


async def test_quiet_hours_cap_on_three_speed_fan_is_idempotent(house: House, monkeypatch: Any) -> None:
    """Spec §6.4/§9.3 — capped fan must not be rewritten every tick; release must still happen."""
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool down")
    await house.add_zone(
        "Bedroom", 75, allow_fan_control=True, fan_entities=["fan.bedroom"],
    )
    writes = _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.add_schedule("Evening", "18:00", "23:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 20, 0)
    await main_mod.execute_active_mode()
    first = list(writes)
    assert house.ha.entities["fan.bedroom"].attributes["percentage"] <= 50, writes
    for i in range(1, 5):
        house.at(*MON, 20, 5 * i)
        await main_mod.execute_active_mode()
    after_hold = list(writes)
    # Rooms reach target: raised fan must be released to baseline (off).
    house.set_room("Bedroom", 72)
    for i in range(5, 8):
        house.at(*MON, 20, 5 * i)
        await main_mod.execute_active_mode()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert after_hold == first and pct == 0, f"fan writes={writes}, final pct={pct}"


async def test_three_speed_fan_at_75_is_released(house: House, monkeypatch: Any) -> None:
    """Spec §6.4 — a fan we raised to the LLM's '~75' is released when rooms reach target,
    and on leaving AI mode; our own stepped readback is not a 'hand change'."""
    llm = _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=75\nREASON: cool down")
    await house.add_zone(
        "Bedroom", 74, allow_fan_control=True, fan_entities=["fan.bedroom"],
    )
    writes = _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert writes == [75], writes
    house.set_room("Bedroom", 74.5)  # state changed → LLM asked again
    house.at(*MON, 14, 6)
    await main_mod.execute_active_mode()
    # Rooms reach target → release expected.
    llm.answer = "RECOMMENDED_TEMP: 72\nFAN_ACTIONS:\nREASON: hold"
    house.set_room("Bedroom", 72)
    for m in (12, 18, 24):
        house.at(*MON, 14, m)
        await main_mod.execute_active_mode()
    # And leaving AI mode must give the fan back too.
    await house.set_mode(SystemMode.scheduled)
    house.at(*MON, 14, 30)
    await main_mod.execute_active_mode()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct == 0, (
        f"fan left at {pct}% (writes={writes}, record={main_mod._fan_baseline.get('fan.bedroom')}, "
        f"user_owned={'fan.bedroom' in main_mod._fan_user_owned})"
    )


async def test_follow_me_still_presence_not_dropped_without_dwell(house: House) -> None:
    """Spec §6b.1/§6b.3 — a presence sensor that stays 'on' (person sitting still) must not
    make the zone vanish after 15 min, skipping the 10-min vacancy dwell → setback."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.models.database import Sensor, SensorReading, get_session_maker
    from backend.models.enums import SensorType

    band = {"temp_min": 21, "temp_max": 23}
    bed = await house.add_zone("Bedroom", 70, comfort_preferences=band)
    await house.add_zone(
        "Living Room", 70, comfort_preferences=band, ha_entities=["binary_sensor.living_motion"],
    )
    house.ha.entities["binary_sensor.living_motion"] = EntityState(
        entity_id="binary_sensor.living_motion", state="off",
        attributes={"device_class": "motion"}, last_changed="2026-11-09T08:00:00+00:00",
    )
    # Bedroom presence sensor: turned "on" at 12:38 and still on (HA live state).
    house.ha.entities["binary_sensor.bedroom_presence"] = EntityState(
        entity_id="binary_sensor.bedroom_presence", state="on",
        attributes={"device_class": "occupancy"}, last_changed="2026-11-09T17:38:00+00:00",
    )
    async with get_session_maker()() as db:
        s = Sensor(zone_id=bed, name="Bedroom presence", type=SensorType.presence_only,
                   ha_entity_id="binary_sensor.bedroom_presence")
        db.add(s)
        await db.flush()
        db.add(SensorReading(sensor_id=s.id, zone_id=bed, presence=True,
                             recorded_at=datetime(2026, 11, 9, 12, 38, tzinfo=ZoneInfo("America/New_York"))))
        await db.commit()
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=70)
    house.at(*MON, 12, 45)
    await main_mod.execute_follow_me_mode()
    assert house.ha.would_heat(), "bedroom occupied → heat toward 71.6°F"
    house.at(*MON, 12, 55)  # presence still on, 17 min since its last state change
    await main_mod.execute_follow_me_mode()
    assert house.ha.would_heat(), f"set back while bedroom still occupied: setpoint {house.ha.setpoint_f}"


async def test_stale_followme_tick_switches_mode_after_leaving_followme(house: House, monkeypatch: Any) -> None:
    """Spec §2: every automatic write re-checks the mode right before writing."""
    from backend.api.routes.system import apply_system_mode

    await house.add_zone("Living Room", 66, comfort_preferences={"temp_min": 21, "temp_max": 23})
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="cool", setpoint_f=76, reading_f=66)
    house.at(*MON, 12, 0)

    async def _occ(_zid: Any, _db: Any) -> bool:
        return True

    monkeypatch.setattr(main_mod, "infer_zone_occupancy", _occ)
    real_select = main_mod._auto_select_hvac_mode
    parked, release = asyncio.Event(), asyncio.Event()

    async def _slow_select(*a: Any, **k: Any) -> Any:
        res = await real_select(*a, **k)
        parked.set()
        await release.wait()
        return res

    monkeypatch.setattr(main_mod, "_auto_select_hvac_mode", _slow_select)
    tick = asyncio.create_task(main_mod.execute_follow_me_mode())
    await asyncio.wait_for(parked.wait(), 5)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)  # no schedule -> nobody owns
    release.set()
    await tick
    switches = [p for _, s, p in house.ha.writes() if s == "set_hvac_mode"]
    assert not switches, f"stale Follow-Me tick wrote {switches}; heating now={house.ha.would_heat()}"


async def test_stale_maintenance_tick_switches_mode_after_switch_to_active(house: House, monkeypatch: Any) -> None:
    from backend.api.routes.system import apply_system_mode

    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="cool", setpoint_f=76, reading_f=66)
    house.at(*MON, 12, 0)
    real_select = main_mod._auto_select_hvac_mode
    parked, release = asyncio.Event(), asyncio.Event()

    async def _slow_select(*a: Any, **k: Any) -> Any:
        res = await real_select(*a, **k)
        parked.set()
        await release.wait()
        return res

    monkeypatch.setattr(main_mod, "_auto_select_hvac_mode", _slow_select)
    tick = asyncio.create_task(main_mod.maintain_climate_offset())
    await asyncio.wait_for(parked.wait(), 5)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.active)
    release.set()
    await tick
    switches = [p for _, s, p in house.ha.writes() if s == "set_hvac_mode"]
    assert not switches, f"stale Scheduled tick wrote {switches} after switch to Active"


async def test_manual_override_on_off_thermostat_never_runs(house: House) -> None:
    """Spec §2: heat/cool is chosen from the rooms so the override actually works."""
    from backend.core.manual_control import apply_manual_setpoint

    await house.add_zone("Living Room", 70.5)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="off", setpoint_f=69, reading_f=70.5)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        res = await apply_manual_setpoint(db, 71, source="dashboard")
    # Room cools a lot during the override; automation is paused.
    house.set_room("Living Room", 64)
    house.ha.set_thermostat(reading_f=64)
    for i in range(1, 4):
        house.at(*MON, 12, i)
        await house.tick_scheduled()
    assert house.ha.would_heat(), (
        f"override {res} left thermostat {house.ha.mode} @ {house.ha.setpoint_f}; room 64F, wants 71F"
    )


async def test_chat_zone_tool_ignores_hold_minutes(house: House, monkeypatch: Any) -> None:
    from backend.api.routes.chat import _execute_tool_call
    from backend.config import get_settings as _gs
    from backend.core.manual_override import get_active_override

    monkeypatch.setattr(_gs(), "home_assistant_token", "x")
    zid = await house.add_zone("Living Room", 68)
    await house.add_thermostat_device("Living Room")
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        out = await _execute_tool_call(
            "set_zone_temperature", {"zone_id": str(zid), "target_c": 22.0, "hold_minutes": 30}, db
        )
    assert out.get("success"), out
    async with get_session_maker()() as db:
        ov = await get_active_override(db)
    assert ov is not None
    assert ov.get("duration_hours") == 0.5, f"hold_minutes=30 ignored: override {ov}"


async def test_chat_zone_tool_fails_for_sensor_only_zone(house: House, monkeypatch: Any) -> None:
    """Sensor-only zone (the normal setup): /command treats it as a manual override, the tool errors."""
    from backend.api.routes.chat import _execute_tool_call
    from backend.config import get_settings as _gs

    monkeypatch.setattr(_gs(), "home_assistant_token", "x")
    zid = await house.add_zone("Bedroom", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        out = await _execute_tool_call(
            "set_zone_temperature", {"zone_id": str(zid), "target_c": 22.0}, db
        )
    assert out.get("success"), out


async def test_rejected_override_wipes_existing_override(house: House) -> None:
    """A rejected request (direction locked) must not cancel the user's existing override."""
    from backend.api.routes.system import QuickActionRequest, execute_quick_action
    from backend.core.manual_control import apply_manual_setpoint
    from backend.core.manual_override import get_active_override

    await house.add_zone("Living Room", 68)
    await house.add_schedule("Day", "06:00", "22:00", 68, ["Living Room"])
    await house.set_setting("hvac_control_mode", "heat")  # winter heat lock
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 72, source="dashboard")
    assert house.ha.setpoint_f == 72
    async with get_session_maker()() as db:
        resp = await execute_quick_action(QuickActionRequest(action="boost_cool"), db)
    assert resp.success is False, resp
    async with get_session_maker()() as db:
        ov = await get_active_override(db)
    house.at(*MON, 12, 1)
    await house.tick_scheduled()
    assert ov is not None, f"rejected boost_cool ({resp.message}) deleted the 72F override"
    assert house.ha.setpoint_f == 72, f"setpoint reverted to {house.ha.setpoint_f}"


async def test_drift_reasserts_override(house: House) -> None:
    from backend.core.manual_control import apply_manual_setpoint
    from backend.integrations.ha_websocket import HAStateChange

    await house.add_zone("Living Room", 68)
    await house.add_schedule("Day", "06:00", "22:00", 68, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 72, source="dashboard")
    house.ha.set_thermostat(setpoint_f=64)
    st = await house.ha.get_state("climate.thermostat")
    await main_mod._handle_climate_state_change(HAStateChange(
        entity_id="climate.thermostat", domain="climate", state=st.state, attributes=st.attributes))
    assert house.ha.setpoint_f == 72
