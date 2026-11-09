"""Gap handback flag is never reset by a mode change: Scheduled(gap, handed
back) -> Active (AI writes a hold) -> Scheduled in the same gap: the AI hold
stays forever; the Ecobee program never gets control back."""
import backend.api.main as main_mod
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_gap_handback_after_mode_roundtrip(house: House) -> None:
    await house.add_zone("Living Room", 64)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=63)
    house.at(*MON, 23, 0)
    await house.tick_scheduled()  # gap: handback #1
    assert len([c for c in house.ha.calls if c[1] == "resume_program"]) == 1
    from backend.api.routes.system import apply_system_mode
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.active)
    house.at(*MON, 23, 5)
    await main_mod.execute_active_mode()
    print("active wrote", house.ha.mode, house.ha.setpoint_f, [c for c in house.ha.calls if c[0]=="climate"])
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    house.ha.calls.clear()
    for m in (10, 11, 12):
        house.at(*MON, 23, m)
        await house.tick_scheduled()
    print("calls after back to scheduled", house.ha.calls, house.ha.setpoint_f)
    assert [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"back in Scheduled mode in a gap, the AI-mode hold ({house.ha.setpoint_f}F) "
        "is never handed back to the Ecobee program"
    )
