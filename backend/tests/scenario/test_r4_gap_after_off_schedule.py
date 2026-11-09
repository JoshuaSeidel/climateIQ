"""An 'off' schedule (ClimateIQ turned the Ecobee off) ends into a gap.
The gap handback only calls resume_program, which doesn't change hvac_mode,
so the Ecobee stays OFF and its program can't heat the house until the next
schedule.  Learn's handback (spec §2) restores heat_cool in this exact case."""
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_off_schedule_into_gap_leaves_house_off(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    await house.add_schedule("Night", "22:00", "06:00", 68, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    house.set_room("Living Room", 60)
    house.ha.set_thermostat(reading_f=60)
    for h in (17, 18, 20):
        house.at(*MON, h, 1)
        await house.tick_scheduled()
    print("gap", house.ha.mode, [c for c in house.ha.calls if c[0] in ("ecobee",)])
    assert house.ha.mode != "off", (
        "17:00-22:00 gap after an 'off' schedule: ClimateIQ 'handed back' to the "
        "Ecobee program but left the thermostat OFF (rooms 60F) — nothing can heat"
    )
