from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

async def test_followme_restart_in_gap_wipes_followme_hold(house: House, monkeypatch: Any) -> None:
    await house.set_mode(SystemMode.follow_me)
    await house.add_zone("Living", 71, comfort_preferences={"temp_min": 21, "temp_max": 23})
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=71)
    async def _occ(zid: Any, _db: Any) -> bool | None:
        return True
    monkeypatch.setattr(main_mod, "infer_zone_occupancy", _occ)
    house.at(*MON, 12, 0)
    await main_mod.execute_follow_me_mode()
    house.at(*MON, 12, 5)
    await main_mod.execute_follow_me_mode()
    house.ha.calls.clear()
    # add-on restart: in-memory state gone, DB state loaded again
    main_mod._zone_occupancy_stable.clear()
    main_mod._follow_me_observed = False
    main_mod._runtime_state_loaded = False
    main_mod._gap_handed_back = False
    house.at(*MON, 12, 6)
    await main_mod.maintain_climate_offset()
    resumes = [c for c in house.ha.calls if c[1] == "resume_program"]
    print(house.ha.calls)
    assert not resumes, f"Follow-Me gap handback fired after restart, wiping Follow-Me's own hold: {house.ha.calls}"
