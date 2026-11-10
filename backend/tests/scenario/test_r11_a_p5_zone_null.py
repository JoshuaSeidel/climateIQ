"""PUT /zones/{id} with explicit nulls must not persist JSON null into fields every reader expects to be a dict/list."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.tests.scenario.harness import House

FIELDS = ["comfort_preferences", "thermal_profile", "exclude_months", "ha_entities", "fan_entities"]


async def test_null_field_then_list_zones(house: House, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.api.main as m

    # A ZoneManager / pooled DB connection left by an earlier test may hold a
    # lock bound to a closed event loop: start from a clean engine.
    monkeypatch.setattr(m.app_state, "zone_manager", None)
    from backend.models.database import close_db, init_db

    await close_db()
    await init_db()
    zid = await house.add_zone("Bedroom", 64)
    await house.add_zone("Other", 64)
    # One test (not parametrized): the app's middleware holds loop-bound locks.
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        for field in FIELDS:
            r = await c.put(f"/api/v1/zones/{zid}", json={field: None})
            lst = await c.get("/api/v1/zones")
            assert r.status_code in (200, 422) and lst.status_code == 200, (
                f"{field}=null: put {r.status_code}, GET /zones {lst.status_code}"
            )
        for field in ("name", "is_active"):
            r = await c.put(f"/api/v1/zones/{zid}", json={field: None})
            assert r.status_code == 422, f"{field}=null accepted ({r.status_code})"
