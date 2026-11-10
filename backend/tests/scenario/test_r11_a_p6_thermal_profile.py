"""thermal_profile is a user-writable free dict; non-numeric values crash the advisor prompt builder inside maintain_climate_offset."""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

async def test_bad_thermal_profile_blocks_tick(house: House) -> None:
    await house.set_setting("ai_advisor_enabled", True)
    zid = await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Bedroom"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/api/v1/zones/{zid}", json={"thermal_profile": {"heating_rate_c_per_hour": "fast", "occupancy_score_by_hour": 5}})
        print("PUT", r.status_code)
        assert r.status_code == 422, "free-form thermal_profile accepted"
    house.ha.set_thermostat(mode="heat", setpoint_f=60, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print(house.ha.mode, house.ha.setpoint_f, house.ha.action)
    assert house.ha.would_heat()
