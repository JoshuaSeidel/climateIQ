"""Raised fan stays raised when the governing schedule's zones are all excluded (call_llm=False, _settled False)."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _install_stepped_fan, _use_llm

MON = (2026, 11, 9)

async def test_fan_released_when_next_schedule_zones_excluded(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool down")
    await house.add_zone("Attic", 70, exclude_from_metrics=True)
    await house.add_zone("Bedroom", 75, allow_fan_control=True, fan_entities=["fan.bedroom"])
    _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.add_schedule("Day", "08:00", "12:00", 72, ["Bedroom"])
    await house.add_schedule("Eve", "12:00", "20:00", 72, ["Attic"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 11, 0)
    await main_mod.execute_active_mode()
    assert house.ha.entities["fan.bedroom"].attributes["percentage"] == 100
    house.set_room("Bedroom", 71)
    for m in (10, 20, 30, 40, 50):
        house.at(*MON, 14, m)
        await main_mod.execute_active_mode()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct == 0, f"fan still {pct}% hours after raise, room below target"
