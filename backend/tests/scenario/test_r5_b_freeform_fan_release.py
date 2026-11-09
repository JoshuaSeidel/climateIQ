"""Probe §6.4/§6.2: in freeform AI mode (no schedule) with zones that have no own comfort
band, the governing target is the default comfort range (§6.2) -- but the fan-release
"settled" check treats an unbanded zone as always settled, so a fan the AI raised for a
76 degF room (default midpoint 71.6) is released on the very next tick."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _install_stepped_fan, _use_llm

MON = (2026, 11, 9)


async def test_freeform_unbanded_zone_fan_not_released_while_hot(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool down")
    await house.add_zone("Bedroom", 77, allow_fan_control=True, fan_entities=["fan.bedroom"])
    writes = _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert house.ha.entities["fan.bedroom"].attributes["percentage"] == 100, writes
    # Room is still 77 degF (default comfort max 24 C = 75.2 F).  LLM keeps asking for 100.
    for m in (6, 12):
        house.at(*MON, 14, m)
        await main_mod.execute_active_mode()
    pct = house.ha.entities["fan.bedroom"].attributes["percentage"]
    assert pct == 100, f"fan released to {pct}% while room is 77F > default band; writes={writes}"
