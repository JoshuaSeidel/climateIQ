"""§8b: a latched seasonal escape valve must not close because the weather
entity is briefly unavailable (that's missing data, not a recovery)."""
from backend.integrations.ha_client import EntityState
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_escape_valve_survives_weather_blip(house: House) -> None:
    await house.add_zone("Living Room", 64)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    await house.set_setting("weather_entity", "weather.home")
    await house.set_setting("seasonal_lock", {"enabled": True, "seasons": [{
        "name": "LateSummer", "start_month": 11, "start_day": 1,
        "end_month": 11, "end_day": 30, "preferred_mode": "cool",
        "override_outdoor_below_c": 4.4,
    }]})
    house.ha.entities["weather.home"] = EntityState(
        entity_id="weather.home", state="cloudy",
        attributes={"temperature": 35, "temperature_unit": "°F"},
    )
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=66)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.would_heat(), "precondition: escape open, heat runs"

    # Weather integration blips (HA restart of the integration, API outage).
    house.ha.entities["weather.home"] = EntityState(
        entity_id="weather.home", state="unavailable", attributes={},
    )
    house.at(*MON, 12, 1)
    await house.tick_scheduled()
    assert house.ha.would_heat(), (
        f"heat parked at {house.ha.setpoint_f}F (reading 66F, rooms 64F, target 70F) "
        "because the weather entity was unavailable for one tick"
    )
