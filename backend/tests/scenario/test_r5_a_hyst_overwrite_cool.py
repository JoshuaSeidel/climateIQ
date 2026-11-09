"""Cool mirror of the 5.4 hysteresis churn."""
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_cool_must_run_setpoint_is_rewritten(house: House) -> None:
    await house.add_zone("Bedroom", 74.6)
    await house.add_schedule("Day", "06:00", "22:00", 74, ["Bedroom"])
    house.ha.set_thermostat(mode="cool", setpoint_f=80, reading_f=77)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    first = house.ha.setpoint_f
    house.set_room("Bedroom", 74.4)  # inside the 0.5F run threshold, still above target
    house.ha.calls.clear()
    house.at(*MON, 12, 1)
    await house.tick_scheduled()
    rewrites = [c for c in house.ha.calls if c[1] == "set_temperature"]
    print("first", first, "after", house.ha.setpoint_f)
    assert not rewrites, f"cool setpoint {first}F rewritten to {house.ha.setpoint_f}F a minute later while AC still running"
