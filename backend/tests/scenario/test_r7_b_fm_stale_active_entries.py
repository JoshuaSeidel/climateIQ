"""Spec 6b.1/9.2: if no zone has occupancy data Follow-Me changes nothing.
Active mode feeds 'no data' as vacant into the SHARED _zone_occupancy_stable, so after
a switch Active -> Follow-Me the unsignalled zone counts as 'known & vacant' -> setback."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _use_llm

MON = (2026, 11, 9)


async def test_followme_no_data_after_active_is_not_setback(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 72\nREASON: x")
    await house.add_zone("Living Room", 72, comfort_preferences={"temp_min": 21, "temp_max": 23})
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=72)
    await house.set_mode(SystemMode.active)
    house.at(*MON, 12, 0)
    await main_mod.execute_active_mode()
    assert not main_mod._zone_occupancy_stable, "unknown occupancy must not enter the shared dwell state"
    await house.set_mode(SystemMode.follow_me)
    house.ha.calls.clear()
    house.at(*MON, 12, 5)
    await main_mod.execute_follow_me_mode()
    w = [(s, p) for _, s, p in house.ha.writes()]
    assert not w, f"Follow-Me with NO occupancy data wrote {w} (setback from Active's phantom 'vacant')"
