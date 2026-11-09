"""§6.4: fan override state survives restarts.  One Redis read error at first restore
sets `_fan_baseline_restored` before reading -> never retried and the next persist
overwrites the saved state with empty memory."""
from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

import backend.api.main as main_mod
from backend.tests.scenario.harness import House


async def test_fan_restore_not_retried(house: House, monkeypatch: Any) -> None:
    r = aioredis.from_url("redis://localhost:56379/13")
    await r.delete(main_mod._FAN_BASELINE_REDIS_KEY)
    monkeypatch.setattr(main_mod.app_state, "redis_client", r)
    main_mod._fan_baseline.clear()
    main_mod._fan_user_owned.clear()
    main_mod._fan_baseline["fan.a"] = {"baseline": 30}
    await main_mod._persist_fan_baseline()
    main_mod._fan_baseline.clear()
    main_mod._fan_baseline_restored = False
    real_get = r.get
    calls = {"n": 0}
    async def flaky(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("redis blip")
        return await real_get(*a, **k)
    monkeypatch.setattr(r, "get", flaky)
    await main_mod._restore_fan_baseline_once()
    await main_mod._restore_fan_baseline_once()
    await r.aclose()
    assert "fan.a" in main_mod._fan_baseline, "fan baseline never restored after one transient Redis error"
