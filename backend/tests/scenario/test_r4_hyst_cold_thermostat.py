from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_flicker_at_target_churns_with_cold_thermostat(house: House) -> None:
    """Hallway thermostat reads 65°F while the bedroom holds 69°F (target 69,
    max offset 3°F).  The bedroom sensor flickers 69.2 <-> 68.8 (0.2°F below
    target: inside the new hysteresis band, below the ½°F run threshold).
    Heat must stay off and the setpoint must not be rewritten every tick."""
    await house.set_setting("max_temp_offset_f", 3.0)
    await house.add_zone("Bedroom", 69.2)
    await house.add_schedule("Night", "21:00", "07:00", 69, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=64, reading_f=65)
    sps = []
    for i in range(10):
        house.set_room("Bedroom", 69.2 if i % 2 == 0 else 68.8)
        house.at(2026, 11, 9, 23, i)
        await house.tick_scheduled()
        sps.append((house.ha.setpoint_f, house.ha.would_heat()))
    print(sps)
    writes = [c for c in house.ha.calls if c[1] == "set_temperature"]
    print(len(writes), "writes")
    assert len(writes) <= 1 and not any(h for _, h in sps), (
        f"{len(writes)} writes in 10 min; heat toggled: {sps}"
    )


async def test_flicker_at_target_churns_with_hot_thermostat_cool(house: House) -> None:
    """Cool mirror: thermostat in a sunny hallway reads 77°F, rooms hold 73°F
    (target 73, max offset 3°F), sensor flickers 72.8 <-> 73.2."""
    await house.set_setting("max_temp_offset_f", 3.0)
    await house.add_zone("Living", 72.8)
    await house.add_schedule("Evening", "18:00", "22:00", 73, ["Living"])
    house.ha.set_thermostat(mode="cool", setpoint_f=78, reading_f=77)
    sps = []
    for i in range(10):
        house.set_room("Living", 72.8 if i % 2 == 0 else 73.2)
        house.at(2026, 7, 13, 19, i)
        await house.tick_scheduled()
        sps.append((house.ha.setpoint_f, house.ha.would_cool()))
    print(sps)
    writes = [c for c in house.ha.calls if c[1] == "set_temperature"]
    assert len(writes) <= 1 and not any(c for _, c in sps), (
        f"{len(writes)} writes in 10 min; cool toggled: {sps}"
    )
