"""A user's off must not be undone because of a stale 'ClimateIQ turned it off' record.

ClimateIQ turned the thermostat off days ago; the user turned it back on (a tick
sees that), and later turned it off again while the sensors are down."""
from datetime import UTC, datetime, timedelta

import backend.api.main as main_mod
from backend.core.manual_control import climate_entity
from backend.integrations.ha_client import EntityState
from backend.tests.scenario.harness import House


async def test_stale_off_record(house: House) -> None:
    await house.add_zone("Living Room", 70.0)
    await house.add_schedule("Day", "06:30", "22:00", 72, ["Living Room"])
    async with main_mod.get_session_maker()() as db:
        ent = await climate_entity(db)
    main_mod._mode_switch_loaded = True
    main_mod._last_mode_switch.clear()
    main_mod._last_mode_switch[ent] = ("off", datetime.now(UTC) - timedelta(days=3))
    # The user turned it back on: a normal tick observes a non-off mode.
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=70)
    house.at(2026, 11, 9, 11, 0)
    await house.tick_scheduled()
    # ...and later turns it off again while the sensor is down.
    house.ha.set_thermostat(mode="off", setpoint_f=72, reading_f=70)
    house.ha.entities["sensor.living_room_temperature"] = EntityState(
        entity_id="sensor.living_room_temperature", state="unavailable", attributes={})
    house.at(2026, 11, 9, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off", "user's off overridden by a stale record"
