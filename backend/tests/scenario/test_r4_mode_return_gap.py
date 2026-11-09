import backend.api.main as main_mod
from backend.api.routes.system import apply_system_mode
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_active_back_to_scheduled_in_gap_no_handback(house: House) -> None:
    await house.add_zone("Living", 64)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living"])
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=66)
    house.at(*MON, 23, 0)
    await house.tick_scheduled()
    assert main_mod._gap_handed_back
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.active)
    house.at(*MON, 23, 5)
    await main_mod.execute_active_mode()
    print("after active", house.ha.mode, house.ha.setpoint_f, [c[1] for c in house.ha.calls])
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    house.ha.calls.clear()
    for m in (10, 11, 30):
        house.at(*MON, 23, m)
        await house.tick_scheduled()
    print("after back", house.ha.mode, house.ha.setpoint_f, [c[1] for c in house.ha.calls])
    assert [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"back in Scheduled mode in a gap, the AI's {house.ha.setpoint_f}°F hold is never handed back"
    )
