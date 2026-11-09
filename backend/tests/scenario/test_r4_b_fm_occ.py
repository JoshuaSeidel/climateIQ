"""Probe §6b.1/§6b.3: a zone with no fresh signal keeps its last *stable* state;
a single blip must not move the target."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_blip_then_signal_loss_becomes_occupied(house: House, monkeypatch: Any) -> None:
    await house.add_zone("Living Room", 72.5, comfort_preferences={"temp_min": 22, "temp_max": 24})
    await house.add_zone("Guest Room", 72.5, comfort_preferences={"temp_min": 19, "temp_max": 21})
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=72.5)
    occ: dict[str, bool | None] = {}
    lr, gr = str(house.zones["Living Room"]), str(house.zones["Guest Room"])

    async def _occ(zid: Any, _db: Any) -> bool | None:
        return occ[str(zid)]

    monkeypatch.setattr(main_mod, "infer_zone_occupancy", _occ)
    # Long-established: living room occupied, guest room empty.
    occ.update({lr: True, gr: False})
    house.at(*MON, 12, 0)
    await main_mod.execute_follow_me_mode()
    sp0 = house.ha.setpoint_f
    # One motion blip in the guest room on a single tick ...
    occ[gr] = True
    house.at(*MON, 12, 2)
    await main_mod.execute_follow_me_mode()
    # ... then its motion sensor drops off (unavailable → no signal).
    occ[gr] = None
    for m in (4, 6, 8, 10, 12):
        house.at(*MON, 12, m)
        await main_mod.execute_follow_me_mode()
    stable = main_mod._zone_occupancy_stable[gr]["stable"]
    assert stable is False and house.ha.setpoint_f == sp0, (
        f"guest room became occupied from one blip + signal loss; "
        f"setpoint {sp0} → {house.ha.setpoint_f}"
    )
