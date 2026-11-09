from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

async def test_stale_high_setpoint_stands_when_target_drops(house: House) -> None:
    """Prior schedule left setpoint 78F (target 70 + 8F max offset). New schedule target 70;
    room 69.8F (0.2F below: inside the 'between' branch). Old v1.0.94 capped at reading+1;
    now setpoint 78 stands -> heat runs until thermostat reads 78 (room ~8F over target)."""
    await house.add_zone("Bedroom", 69.8)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=78, reading_f=69)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print("sp", house.ha.setpoint_f, "heats", house.ha.would_heat())
    assert house.ha.setpoint_f is not None and house.ha.setpoint_f <= 71, f"setpoint {house.ha.setpoint_f} left standing, heat would run to 78 for a 70F target"
