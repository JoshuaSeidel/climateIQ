"""Deleting a zone leaves its UUID in schedules.zone_ids (JSONB, no FK); schedule then targets a ghost zone."""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

async def test_schedule_after_zone_deleted(house: House) -> None:
    gone = await house.add_zone("Gone", 64)
    await house.add_zone("Kept", 64)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Gone"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete(f"/api/v1/zones/{gone}")
        print("DEL", r.status_code)
        s = await c.get("/api/v1/schedules")
        print("SCHED", s.json()[0]["zone_ids"], s.json()[0]["zone_names"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        sched = (await c.get("/api/v1/schedules")).json()[0]
    # The ghost id is gone, and a schedule that only covered the zone is disabled
    # (an empty zone_ids list would silently mean "all zones").
    assert sched["zone_ids"] == [], sched
    assert sched["is_enabled"] is False, sched
