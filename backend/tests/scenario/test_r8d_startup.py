"""§9.1 analogue: Redis/DB that come up after the add-on starts are picked up."""
import asyncio
from typing import Any

import pytest
import redis.asyncio as aredis

from backend.api import main as m


async def test_redis_down_at_boot_is_reacquired(monkeypatch: pytest.MonkeyPatch) -> None:
    real_from_url = aredis.from_url
    calls = {"n": 0}

    class FlakyPing:
        def __init__(self, c: Any) -> None:
            self._c = c

        def __getattr__(self, k: str) -> Any:
            return getattr(self._c, k)

        async def ping(self) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("redis not up yet")
            return await self._c.ping()

    monkeypatch.setattr(m.redis, "from_url", lambda *a, **k: FlakyPing(real_from_url(*a, **k)))
    monkeypatch.setattr(m, "_REDIS_RETRY_S", 0.1)
    monkeypatch.setattr(m.app_state, "redis_client", None)
    monkeypatch.setattr(m, "_redis_retry_task", None)
    m.app_state.redis_client = await m.init_redis()
    assert m.app_state.redis_client is None
    task = m._redis_retry_task
    assert task is not None
    await asyncio.wait_for(task, timeout=5)
    assert m.app_state.redis_client is not None


async def test_db_down_at_boot_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def flaky_init() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("db not up yet")

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(m, "init_db", flaky_init)
    monkeypatch.setattr(m.asyncio, "sleep", no_sleep)
    await m._init_db_with_retry()
    assert calls["n"] == 3


async def test_db_permanently_down_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> None:
        raise OSError("db down")

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(m, "init_db", boom)
    monkeypatch.setattr(m.asyncio, "sleep", no_sleep)
    with pytest.raises(OSError, match="db down"):
        await m._init_db_with_retry()
