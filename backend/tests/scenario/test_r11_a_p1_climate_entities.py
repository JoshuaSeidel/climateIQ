"""PUT /settings accepts any string as climate_entities; a non-climate entity silently stops all heating."""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_bad_climate_entity_rejected_at_write(house: House) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for bad in ("sensor.kitchen_temperature", ",climate.thermostat", "ecobee", "climate.ok,sensor.x"):
            r = await c.put("/api/v1/settings", json={"climate_entities": bad})
            print(repr(bad), r.status_code)
            assert r.status_code == 422, f"{bad!r} accepted ({r.status_code})"


async def test_bad_climate_entity_stops_heat(house: House) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/api/v1/settings", json={"climate_entities": ",climate.thermostat"})
        print(r.status_code)
    await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=60, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print(house.ha.mode, house.ha.setpoint_f, house.ha.action)
    assert house.ha.would_heat()
