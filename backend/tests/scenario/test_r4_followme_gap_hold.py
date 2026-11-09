"""Follow-Me mode, schedule ends, no occupancy data: the schedule's
offset-compensated hold (target+max offset) outlives the schedule forever —
the same bug round 3 fixed for Scheduled mode."""
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_followme_gap_keeps_compensated_hold(house: House) -> None:
    await house.set_mode(SystemMode.follow_me)
    await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=60, reading_f=69)
    house.at(*MON, 21, 59)
    await house.tick_all()
    print("21:59", house.ha.setpoint_f)
    assert house.ha.setpoint_f and house.ha.setpoint_f >= 75
    house.set_room("Bedroom", 71)
    house.ha.set_thermostat(reading_f=72)
    house.ha.calls.clear()
    for h, m in ((22, 0), (23, 0), (2, 0)):
        house.at(2026, 11, 9 if h >= 22 else 10, h, m)
        await house.tick_all()
    print("after", house.ha.mode, house.ha.setpoint_f, house.ha.calls)
    assert house.ha.setpoint_f < 72 or [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"Follow-Me gap: the schedule's {house.ha.setpoint_f}F compensated hold "
        "keeps heating all night (rooms 71F, thermostat 72F)"
    )
