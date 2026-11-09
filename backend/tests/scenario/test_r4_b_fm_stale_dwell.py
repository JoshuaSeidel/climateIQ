"""Probe §6b.3: a single motion blip must not move the target — dwell measured
from a raw state last observed hours earlier (before a schedule took over)."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_blip_after_schedule_counts_as_occupied_instantly(house: House, monkeypatch: Any) -> None:
    await house.add_zone("Living Room", 72.5, comfort_preferences={"temp_min": 22, "temp_max": 24})
    await house.add_zone("Guest Room", 72.5, comfort_preferences={"temp_min": 19, "temp_max": 21})
    await house.add_schedule("Work", "08:00", "17:00", 70, ["Living Room"], priority=5)
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=72.5)
    lr, gr = str(house.zones["Living Room"]), str(house.zones["Guest Room"])
    occ: dict[str, bool] = {lr: True, gr: False}

    async def _occ(zid: Any, _db: Any) -> bool:
        return occ[str(zid)]

    monkeypatch.setattr(main_mod, "infer_zone_occupancy", _occ)
    for m in (40, 45, 50):
        house.at(*MON, 7, m)
        await main_mod.execute_follow_me_mode()
    occ[gr] = True  # someone walks past the guest room at 07:58 (one tick)
    house.at(*MON, 7, 58)
    await main_mod.execute_follow_me_mode()
    # 08:00-17:00 the schedule governs; Follow-Me stands down.
    house.at(*MON, 12, 0)
    await main_mod.execute_follow_me_mode()
    # 17:00: another single walk-past on the first gap tick.
    house.at(*MON, 17, 0)
    sp_before = house.ha.setpoint_f
    await main_mod.execute_follow_me_mode()
    stable = main_mod._zone_occupancy_stable[gr]["stable"]
    assert stable is False, (
        f"one blip at 17:00 made the guest room occupied instantly; "
        f"setpoint {sp_before} → {house.ha.setpoint_f}"
    )
