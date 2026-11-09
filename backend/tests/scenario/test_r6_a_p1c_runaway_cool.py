from backend.tests.scenario.harness import House


async def test_stale_low_setpoint_stands_cool(house: House) -> None:
    await house.add_zone("Living", 76.2)
    await house.add_schedule("Day", "06:00", "22:00", 76, ["Living"])
    house.ha.set_thermostat(mode="cool", setpoint_f=68, reading_f=77)
    house.at(2026, 7, 13, 12, 0)
    await house.tick_scheduled()
    print("sp", house.ha.setpoint_f, house.ha.would_cool())
    assert house.ha.setpoint_f is not None and house.ha.setpoint_f >= 75, f"setpoint {house.ha.setpoint_f} left standing; cool would run to 68 for 76 target"
