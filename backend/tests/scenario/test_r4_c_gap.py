"""Gap handback flag goes stale."""
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_resume_in_gap_after_earlier_handback(house: House) -> None:
    from backend.core.manual_control import apply_manual_setpoint, resume_automatic_control

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=70)
    house.at(*MON, 22, 30)
    await house.tick_scheduled()  # first gap tick: hands back once
    assert [c for c in house.ha.calls if c[1] == "resume_program"]
    house.at(*MON, 23, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 78, source="dashboard")
    assert house.ha.setpoint_f == 78
    house.ha.calls.clear()
    # user presses Resume within the same minute (no maintenance tick in between)
    async with get_session_maker()() as db:
        await resume_automatic_control(db, source="resume")
    house.at(*MON, 23, 5)
    await house.tick_scheduled()
    print("after resume", house.ha.mode, house.ha.setpoint_f, house.ha.calls)
    assert [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"Resume in a gap left the 78F override hold in place (setpoint {house.ha.setpoint_f})"
    )


async def test_override_expiring_in_gap_without_tick(house: House) -> None:
    """Override set at 21:59:xx, expires at the 22:00 boundary; no maintenance
    tick saw it active (single_flight skip / timing).  Flag stays True from the
    previous night? Use previous gap handback then a schedule never governs."""
    from backend.core.manual_control import apply_manual_setpoint

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Night", "23:30", "23:45", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=70)
    house.at(*MON, 23, 0)
    await house.tick_scheduled()
    assert [c for c in house.ha.calls if c[1] == "resume_program"]
    house.at(*MON, 23, 29)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 78, source="dial")
    house.ha.calls.clear()
    # boundary at 23:30 ends it; next tick 23:50 in gap
    house.at(*MON, 23, 50)
    await house.tick_scheduled()
    print(house.ha.calls)
    assert [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"override expired in a gap but 78F hold stays (setpoint {house.ha.setpoint_f})"
    )


async def test_mode_switch_back_to_scheduled_in_gap(house: House) -> None:
    from backend.api.routes.system import apply_system_mode

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=70)
    house.at(*MON, 22, 30)
    await house.tick_scheduled()
    assert [c for c in house.ha.calls if c[1] == "resume_program"]
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.active)
    # AI mode leaves a hold (simulate its write)
    await house.ha.set_temperature_with_hold("climate.thermostat", 76, intent_mode="heat")
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    house.ha.calls.clear()
    house.at(*MON, 23, 0)
    await house.tick_scheduled()
    print(house.ha.calls, house.ha.setpoint_f)
    assert [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"back in Scheduled gap, AI's 76F hold kept (setpoint {house.ha.setpoint_f})"
    )
