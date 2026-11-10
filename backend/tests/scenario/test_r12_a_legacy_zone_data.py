"""Stored legacy zone data (old units, string numbers) must never make zone endpoints 500."""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from backend.api.main import app
from backend.models.database import Zone, get_session_maker
from backend.tests.scenario.harness import House


async def test_legacy_stored_zone_data_still_served(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.api.main as m
    from backend.models.database import close_db, init_db

    # A zone manager / pooled DB connection left by an earlier test may hold a
    # lock bound to a closed event loop: start from a clean engine.
    monkeypatch.setattr(m.app_state, "zone_manager", None)
    await close_db()
    await init_db()
    zid = await house.add_zone("Bedroom", 64)
    async with get_session_maker()() as db:
        await db.execute(
            update(Zone)
            .where(Zone.id == zid)
            .values(
                comfort_preferences={"temp_min": 68, "temp_max": 75},
                thermal_profile={"occupancy_score_by_hour": {"1": None}, "data_days": "7"},
                exclude_months=[0, 13],
            )
        )
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/zones")).status_code == 200
        assert (await c.get(f"/api/v1/zones/{zid}")).status_code == 200
        # The user can still fix the zone through the UI.
        r = await c.put(f"/api/v1/zones/{zid}", json={"name": "Bedroom 2"})
        assert r.status_code == 200, r.text
