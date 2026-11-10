"""Zone comfort_preferences is free-form: one zone's non-numeric temp_min poisons the shared poll for all later zones."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.tests.scenario.harness import House


async def test_zone_prefs_validated(house: House) -> None:
    zid = await house.add_zone("Bedroom", 64)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for prefs in ({"temp_min": "abc", "temp_max": 24}, {"temp_min": 26, "temp_max": 20},
                      {"temp_min": 68, "temp_max": 75}, {"lux_max": "bright"}):
            r = await c.put(f"/api/v1/zones/{zid}", json={"comfort_preferences": prefs})
            print(prefs, r.status_code)
            assert r.status_code == 422, f"{prefs} accepted"


async def test_exclude_months_validated(house: House) -> None:
    zid = await house.add_zone("Bedroom", 64)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/api/v1/zones/{zid}", json={"exclude_months": [0, 13, 99]})
        print(r.status_code)
        assert r.status_code == 422


async def test_poll_zone_status_poison(house: House, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.api.main as m
    from backend.core.zone_manager import ZoneManager
    from backend.models.database import get_session_maker
    bad = await house.add_zone("Bad", 70)
    goods = [await house.add_zone(f"G{i}", 70) for i in range(4)]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.put(f"/api/v1/zones/{bad}", json={"comfort_preferences": {"temp_min": "abc", "temp_max": 24}})
        for g in goods:
            await c.put(f"/api/v1/zones/{g}", json={"comfort_preferences": {"temp_min": 20, "temp_max": 24}})
    zm = ZoneManager()
    async with get_session_maker()() as db:
        await zm.refresh_from_db(db)
    monkeypatch.setattr(m.app_state, "zone_manager", zm)
    class _WS:
        async def broadcast(self, *a, **k): pass
    monkeypatch.setattr(m.app_state, "ws_manager", _WS())
    await m.poll_zone_status()
    got = [getattr(zm.get_state(g), "metrics", {}).get("comfort_min_c") for g in goods]
    print(got)
    assert all(v == 20.0 for v in got)
