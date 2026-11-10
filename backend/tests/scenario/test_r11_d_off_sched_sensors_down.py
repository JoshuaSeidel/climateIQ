"""After an 'off' schedule ends while governing sensors are unavailable, the thermostat stays OFF indefinitely (seed 26 of the day-sim)."""
from backend.integrations.ha_client import EntityState
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_off_schedule_end_with_sensors_down(house: House) -> None:
    await house.add_zone("Living Room", 70.0)
    await house.add_schedule("Day", "06:30", "22:00", 72, ["Living Room"])
    await house.add_schedule("Away", "09:00", "11:00", 60, [], hvac_mode="off", priority=9)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=70)
    t = 8 * 60 + 50
    while t <= 14 * 60:
        house.at(*MON, t // 60, t % 60)
        if t >= 10 * 60:  # sensor battery dies during the away block
            house.ha.entities["sensor.living_room_temperature"] = EntityState(
                entity_id="sensor.living_room_temperature", state="unavailable", attributes={})
            house.ha.set_thermostat(reading_f=55.0)  # house is cooling off
        await house.tick_scheduled()
        t += 1
    assert house.ha.mode != "off", "thermostat still OFF 3h after the off-schedule ended; house at 55F (thermostat's own reading)"
