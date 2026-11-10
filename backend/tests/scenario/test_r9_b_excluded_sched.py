"""Spec 2b: schedule's own zones all excluded -> other rooms never stand in; no write that tick."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _install_stepped_fan, _use_llm

MON = (2026, 11, 9)


async def test_excluded_schedule_zones_no_writes(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 66\nFAN_ACTIONS: Bedroom=100\nREASON: cool")
    await house.add_zone("Attic", 70, exclude_from_metrics=True)
    await house.add_zone("Bedroom", 80, allow_fan_control=True, fan_entities=["fan.bedroom"])
    fan_writes = _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Attic"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=75, reading_f=74)
    house.at(*MON, 12, 0)
    await main_mod.execute_active_mode()
    print("MODE", house.ha.mode, house.ha.setpoint_f, "fan", fan_writes, house.ha.writes())
    assert house.ha.writes() == [], house.ha.writes()
    assert fan_writes == []
