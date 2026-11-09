from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_off_schedule_leaves_next_schedule_without_hvac(house: House) -> None:
    """An "off" schedule (Away, 08:00-17:00) ends; Evening (72 °F) starts with
    the rooms 1 °F short.  ClimateIQ itself turned the thermostat off, but the
    Evening schedule never turns it back on while the rooms sit inside the
    1 °F direction dead-band — spec §5.4 says a room 1 °F short gets heat."""
    await house.add_zone("Living Room", 66)
    await house.add_schedule("Away", "08:00", "17:00", 62, ["Living Room"], hvac_mode="off")
    await house.add_schedule("Evening", "17:00", "22:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 9, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    house.set_room("Living Room", 71.0)
    house.ha.set_thermostat(reading_f=71.0)
    for m in range(0, 60, 10):
        house.at(*MON, 17, m)
        await house.tick_scheduled()
    print("evening", house.ha.mode, house.ha.setpoint_f)
    assert house.ha.mode == "heat" and house.ha.would_heat(), (
        f"Evening 72°F, rooms 71°F: thermostat left '{house.ha.mode}' for an hour"
    )
