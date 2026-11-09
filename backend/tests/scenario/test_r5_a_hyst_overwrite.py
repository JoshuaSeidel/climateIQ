"""Spec 5.4 hysteresis: a setpoint written while heat had to run may stand until rooms
reach target.  The 'slightly below target' band caps it at max(target, reading+1F), but
the must-run write is the offset-formula value (target+1F..+max), which exceeds that cap,
so crossing the 0.5F run threshold flips the setpoint 70 -> 69 -> 70 ... (write churn)."""
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_must_run_setpoint_is_rewritten_when_room_edges_toward_target(house: House) -> None:
    await house.add_zone("Bedroom", 68.4)
    await house.add_schedule("Night", "06:00", "22:00", 69, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=65, reading_f=66)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    first = house.ha.setpoint_f
    # Room warms 0.2F (still below target, now inside the 0.5F run threshold); thermostat reading unchanged.
    house.set_room("Bedroom", 68.6)
    house.ha.calls.clear()
    house.at(*MON, 12, 1)
    await house.tick_scheduled()
    rewrites = [c for c in house.ha.calls if c[0] == "climate" and c[1] == "set_temperature"]
    print("first", first, "after", house.ha.setpoint_f, rewrites)
    assert not rewrites, (
        f"setpoint {first}F written while heat had to run was rewritten to {house.ha.setpoint_f}F one "
        "minute later with the room still below target and heat still running (hysteresis not honoured)"
    )
