"""FAN_ACTIONS parsing holes: 'inf' aborts remaining pairs; '100%' ignored."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _install_stepped_fan, _use_llm

MON = (2026, 11, 9)


async def _setup(house: House, monkeypatch: Any, answer: str) -> None:
    _use_llm(monkeypatch, answer)
    await house.add_zone("Bedroom", 77, allow_fan_control=True, fan_entities=["fan.bedroom"])
    await house.add_zone("Office", 77, allow_fan_control=True, fan_entities=["fan.office"])
    _install_stepped_fan(house, "fan.bedroom", speeds=3)
    _install_stepped_fan(house, "fan.office", speeds=3)
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()


async def test_percent_sign_ignored(house: House, monkeypatch: Any) -> None:
    await _setup(house, monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100%\nREASON: x")
    assert house.ha.entities["fan.bedroom"].attributes["percentage"] == 100


async def test_inf_aborts_later_pairs(house: House, monkeypatch: Any) -> None:
    await _setup(house, monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=inf|Office=100\nREASON: x")
    assert house.ha.entities["fan.office"].attributes["percentage"] == 100
