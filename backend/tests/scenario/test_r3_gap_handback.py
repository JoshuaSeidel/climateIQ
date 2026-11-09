"""Round 3: in a Scheduled-mode gap control is handed back to the Ecobee program."""

from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_compensation_hold_outlives_schedule(house: House) -> None:
    """Day schedule ends at 22:00 with no night schedule.  The cold bedroom
    pushed the setpoint to target + max offset; after the schedule ends the
    thermostat keeps that offset-compensated hold and heats all night."""
    await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=60, reading_f=69)
    house.at(*MON, 21, 59)
    await house.tick_scheduled()
    print("21:59 setpoint", house.ha.setpoint_f)
    assert house.ha.setpoint_f and house.ha.setpoint_f >= 75
    # Schedule over.  Rooms reach/exceed the old target; thermostat warm.
    house.set_room("Bedroom", 71)
    house.ha.set_thermostat(reading_f=72)
    house.ha.calls.clear()
    for m in range(0, 120, 30):
        house.at(*MON, 22, 0) if m == 0 else house.at(2026, 11, 9, 22 + m // 60, m % 60)
        await house.tick_scheduled()
    print("after", house.ha.mode, house.ha.setpoint_f, house.ha.action)
    handbacks = [c for c in house.ha.calls if c[1] == "resume_program"]
    assert len(handbacks) == 1 and not [c for c in house.ha.calls if c[1] == "set_temperature"], (
        f"heat still running at {house.ha.setpoint_f}°F after the schedule ended "
        f"(reading 72°F, rooms 71°F)"
    )


async def test_resume_in_gap_leaves_override_setpoint(house: House) -> None:
    """Spec §2: Resume clears the override and hands control back.  In a
    Scheduled-mode gap the override's 78 °F hold simply stays."""
    from backend.core.manual_control import apply_manual_setpoint, resume_automatic_control
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=70)
    house.at(*MON, 23, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 78, source="dashboard")
    print("override", house.ha.mode, house.ha.setpoint_f)
    assert house.ha.setpoint_f == 78
    house.ha.calls.clear()
    async with get_session_maker()() as db:
        await resume_automatic_control(db, source="resume")
    house.at(*MON, 23, 5)
    await house.tick_scheduled()
    print("after resume", house.ha.mode, house.ha.setpoint_f, house.ha.action)
    assert len([c for c in house.ha.calls if c[1] == "resume_program"]) == 1, (
        f"after Resume the 78°F override hold is still heating (setpoint {house.ha.setpoint_f})"
    )
