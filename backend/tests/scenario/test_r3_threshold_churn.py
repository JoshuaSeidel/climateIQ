from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_sensor_flicker_at_run_threshold_short_cycles(house: House) -> None:
    """Spec §5.4 / §9.3: a room hovering ~0.5 °F under target (Zigbee sensor
    flickering by one 0.1 °C report step, 69.4 ↔ 69.6 °F) must not flip the
    furnace on/off and rewrite the setpoint every tick."""
    await house.add_zone("Living Room", 69.4)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    states = []
    for i in range(10):
        house.set_room("Living Room", 69.4 if i % 2 == 0 else 69.6)
        house.at(*MON, 12, i)
        await house.tick_scheduled()
        states.append((house.ha.setpoint_f, house.ha.would_heat()))
    writes = [c for c in house.ha.writes() if c[1] == "set_temperature"]
    print(states)
    assert len(writes) <= 2, f"{len(writes)} setpoint writes in 10 min: {states}"


async def test_sensor_flicker_at_run_threshold_short_cycles_cool(house: House) -> None:
    await house.add_zone("Living Room", 74.6)
    await house.add_schedule("Day", "06:00", "22:00", 74, ["Living Room"])
    house.ha.set_thermostat(mode="cool", setpoint_f=74, reading_f=74)
    states = []
    for i in range(10):
        house.set_room("Living Room", 74.6 if i % 2 == 0 else 74.4)
        house.at(*MON, 12, i)
        await house.tick_scheduled()
        states.append((house.ha.setpoint_f, house.ha.would_cool()))
    writes = [c for c in house.ha.writes() if c[1] == "set_temperature"]
    print(states)
    assert len(writes) <= 2, f"{len(writes)} setpoint writes in 10 min: {states}"
