"""Spec 6b.1/6b.2-3: a room whose only occupancy entity is a light: light ON => occupied,
light OFF => (since the r6 change ignores off lights) no signal => Follow-Me keeps the last
stable state ('occupied') FOREVER, so an emptied room is heated indefinitely."""
from __future__ import annotations

import backend.api.dependencies as deps
import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_light_only_room_goes_vacant_when_light_off(house: House) -> None:
    deps._ha_client = house.ha
    await house.add_zone("Office", 70, comfort_preferences={"temp_min": 21, "temp_max": 23}, ha_entities=["light.office"])
    await house.add_zone("Bedroom", 70, comfort_preferences={"temp_min": 17, "temp_max": 19}, ha_entities=["binary_sensor.bed_motion"])
    house.ha.entities["binary_sensor.bed_motion"] = EntityState(
        entity_id="binary_sensor.bed_motion", state="off", attributes={"device_class": "motion"},
        last_changed="2026-11-09T01:00:00+00:00")
    house.ha.entities["light.office"] = EntityState(entity_id="light.office", state="on", attributes={})
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=70)
    house.at(*MON, 12, 0)
    await main_mod.execute_follow_me_mode()
    assert house.ha.would_heat(), "office light on => occupied => heat toward 71.6F"
    # Person leaves, switches the light off.  Quarter of an hour of ticks.
    house.ha.entities["light.office"] = EntityState(entity_id="light.office", state="off", attributes={})
    for m in range(2, 30, 2):
        house.at(*MON, 12, m)
        await main_mod.execute_follow_me_mode()
    assert not house.ha.would_heat(), (
        f"office empty for 28 min (light off) but still heated: setpoint {house.ha.setpoint_f}, "
        f"stable={main_mod._zone_occupancy_stable}")
