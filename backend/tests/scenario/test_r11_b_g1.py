from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

async def test_control_heat_vs_season_cool(house: House) -> None:
    """hvac_control_mode=heat (user: 'heat only') + seasonal lock cool season.
    _auto_select ignores the season when control_mode is set, _direction_allowed
    does not: mode flips off->heat then heat is parked -> cold house, nothing runs,
    and a useless mode switch + notification happen."""
    await house.add_zone("Bed", 64)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    await house.set_setting("hvac_control_mode", "heat")
    await house.set_setting("seasonal_lock", {"enabled": True, "seasons": [{
        "name": "S", "start_month": 1, "start_day": 1, "end_month": 12, "end_day": 31, "preferred_mode": "cool"}]})
    house.ha.set_thermostat(mode="off", reading_f=66)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print("RESULT", house.ha.mode, house.ha.setpoint_f, house.ha.would_heat(), [c[1] for c in house.ha.writes()])
    # Spec §4.4: every restriction only forbids.  Heat is forbidden by the season,
    # so the mode is left alone and no pointless switch is made.
    assert house.ha.mode == "off", house.ha.mode
    assert "set_hvac_mode" not in [c[1] for c in house.ha.writes()], house.ha.writes()
