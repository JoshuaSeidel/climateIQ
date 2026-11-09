from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_off_schedule_then_gap_leaves_ecobee_off(house: House) -> None:
    """Away 09:00-17:00 set to off; nothing after.  At 17:00 the gap hands
    back to the Ecobee program, but the thermostat stays 'off' (ClimateIQ
    turned it off), so the Ecobee program can never heat the house."""
    await house.add_zone("Living", 66)
    await house.add_schedule("Away", "09:00", "17:00", 62, ["Living"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=67)
    house.at(*MON, 9, 1)
    await house.tick_scheduled()
    print("09:01", house.ha.mode)
    assert house.ha.mode == "off"
    house.ha.calls.clear()
    for h, m in ((17, 0), (17, 1), (18, 0), (21, 0)):
        house.at(*MON, h, m)
        await house.tick_scheduled()
    print("calls", house.ha.calls, "mode", house.ha.mode)
    assert [c for c in house.ha.calls if c[1] == "resume_program"]
    assert house.ha.mode != "off", "after the off schedule ended the Ecobee was left off all evening"
