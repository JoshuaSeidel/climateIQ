"""Probe §6.4: raised fans are released when the governing zones reach target."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _install_stepped_fan, _use_llm

MON = (2026, 11, 9)


def _llm_down(monkeypatch: Any) -> None:
    import backend.api.routes.chat as chat_mod

    async def _get(_db: Any) -> Any:
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)


async def _raise_fan(house: House, monkeypatch: Any) -> list[int]:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool down")
    await house.add_zone("Bedroom", 75, allow_fan_control=True, fan_entities=["fan.bedroom"])
    writes = _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert house.ha.entities["fan.bedroom"].attributes["percentage"] == 100
    return writes


async def test_overshoot_past_target_never_releases_fan(house: House, monkeypatch: Any) -> None:
    writes = await _raise_fan(house, monkeypatch)
    _llm_down(monkeypatch)
    house.set_room("Bedroom", 70.5)  # cooled through 72 °F target and past it
    for m in (10, 20, 30, 40, 50):
        house.at(*MON, 14, m)
        await main_mod.execute_active_mode()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct == 0, f"fan still {pct}% with room 1.5°F below cool target; writes={writes}"


async def test_unrelated_constraint_zone_blocks_fan_release(house: House, monkeypatch: Any) -> None:
    writes = await _raise_fan(house, monkeypatch)
    # Unrelated basement, out of its own band; not part of the schedule.
    await house.add_zone("Basement", 60, comfort_preferences={"temp_min": 20, "temp_max": 22})
    _llm_down(monkeypatch)
    house.set_room("Bedroom", 72)  # governing zone exactly on target
    for m in (10, 20, 30, 40, 50):
        house.at(*MON, 14, m)
        await main_mod.execute_active_mode()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct == 0, f"fan still {pct}% with governing zone on target; writes={writes}"
