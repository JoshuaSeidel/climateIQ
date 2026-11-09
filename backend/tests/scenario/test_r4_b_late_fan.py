"""Probe: a fan whose state reports late (HA call_service returns before the
fan's new state is published) is never capped/released."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _use_llm

MON = (2026, 11, 9)


def _install_late_fan(house: House, eid: str) -> dict[str, Any]:
    """Continuous fan: state updates only after flush() (i.e. a moment later)."""
    house.ha.entities[eid] = EntityState(entity_id=eid, state="off", attributes={"percentage": 0})
    ctl: dict[str, Any] = {"pending": None, "writes": []}
    orig = house.ha.call_service

    async def _cs(domain: str, service: str, data: Any = None, target: Any = None, **kw: Any) -> Any:
        payload = dict(data or {})
        if target:
            payload.update(target)
        if domain == "fan" and payload.get("entity_id") == eid:
            house.ha.calls.append((domain, service, payload))
            pct = int(payload.get("percentage", 0)) if service == "set_percentage" else 0
            ctl["writes"].append(pct)
            ctl["pending"] = pct
            return []
        return await orig(domain, service, data, target, **kw)

    def flush() -> None:
        if ctl["pending"] is not None:
            p = ctl["pending"]
            house.ha.entities[eid] = EntityState(
                entity_id=eid, state="on" if p else "off", attributes={"percentage": p}
            )
            ctl["pending"] = None

    ctl["flush"] = flush
    house.ha.call_service = _cs  # type: ignore[method-assign]
    return ctl


async def test_late_reporting_fan_escapes_night_cap(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool down")
    await house.add_zone("Bedroom", 75, allow_fan_control=True, fan_entities=["fan.bedroom"])
    fan = _install_late_fan(house, "fan.bedroom")
    await house.add_schedule("Evening", "17:00", "23:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 18, 50)
    await main_mod.execute_active_mode()
    fan["flush"]()  # the fan reports 100 % a moment later
    assert house.ha.entities["fan.bedroom"].attributes["percentage"] == 100
    for m in (19 * 60 + 0, 19 * 60 + 5, 19 * 60 + 10, 19 * 60 + 15):
        house.at(*MON, m // 60, m % 60)
        await main_mod.execute_active_mode()
        fan["flush"]()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct <= 50, (
        f"fan still at {pct}% in quiet hours; writes={fan['writes']} "
        f"record={main_mod._fan_baseline.get('fan.bedroom')} "
        f"user_owned={'fan.bedroom' in main_mod._fan_user_owned}"
    )


async def test_late_reporting_fan_not_released_leaving_ai(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool down")
    await house.add_zone("Bedroom", 75, allow_fan_control=True, fan_entities=["fan.bedroom"])
    fan = _install_late_fan(house, "fan.bedroom")
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    fan["flush"]()
    await house.set_mode(SystemMode.scheduled)
    for m in (10, 15, 20, 25):
        house.at(*MON, 14, m)
        await main_mod.execute_active_mode()
        fan["flush"]()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct == 0, (
        f"fan left at {pct}% after leaving AI mode; writes={fan['writes']} "
        f"record={main_mod._fan_baseline.get('fan.bedroom')}"
    )
