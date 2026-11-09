import pytest

import backend.api.main as main_mod
from backend.core.manual_control import ManualControlError, apply_manual_setpoint
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_failed_manual_request_rearms_gap_handback(house: House) -> None:
    """Spec §2: a rejected/failed manual request changes nothing; handback is once."""
    await house.add_zone("Living", 66)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living"])
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=66)
    house.at(*MON, 23, 0)
    await house.tick_scheduled()
    assert main_mod._gap_handed_back
    await house.set_setting("hvac_control_mode", "cool")  # heat locked -> manual heat rejected
    house.ha.calls.clear()
    async with get_session_maker()() as db:
        with pytest.raises(ManualControlError):
            await apply_manual_setpoint(db, 75, source="dial")
    house.at(*MON, 23, 5)
    await house.tick_scheduled()
    assert not [c for c in house.ha.calls if c[1] == "resume_program"], (
        "rejected manual request re-armed the gap handback: Ecobee program resumed a second time"
    )
