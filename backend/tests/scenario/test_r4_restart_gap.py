import backend.api.main as main_mod
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_restart_in_gap_hands_back_again(house: House) -> None:
    """Gap after 22:00: ClimateIQ hands back once.  The user then sets a hold
    at the Ecobee itself (the Ecobee owns the gap).  An add-on restart in the
    same gap resumes the program again, cancelling the user's hold."""
    await house.add_zone("Living", 68)
    await house.add_schedule("Day", "06:30", "22:00", 70, ["Living"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 22, 1)
    await house.tick_scheduled()
    assert len([c for c in house.ha.calls if c[1] == "resume_program"]) == 1
    house.ha.calls.clear()
    # restart
    main_mod._runtime_state_loaded = False
    main_mod._mode_switch_loaded = False
    main_mod._gap_handed_back = False
    main_mod._last_offset_temp.clear()
    main_mod._preconditioning.clear()
    main_mod._last_mode_switch.clear()
    house.at(*MON, 23, 30)
    await house.tick_scheduled()
    resumes = [c for c in house.ha.calls if c[1] == "resume_program"]
    assert not resumes, "restart in the same gap handed back a second time (cancels any Ecobee hold)"
