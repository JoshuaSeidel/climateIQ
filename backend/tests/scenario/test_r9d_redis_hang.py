"""A Redis that accepts connections but never answers must not hang the Active-mode tick."""
import asyncio
from typing import Any

import redis.asyncio as aredis

import backend.api.main as m
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House


async def test_silent_redis_does_not_hang_active_tick(house: House, monkeypatch: Any) -> None:
    async def _handler(r: Any, w: Any) -> None:  # accepts, never answers
        await asyncio.sleep(3600)

    srv = await asyncio.start_server(_handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    real_from_url = aredis.from_url
    seen: dict[str, Any] = {}

    def _spy(url: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_from_url(f"redis://127.0.0.1:{port}/0", **kwargs)

    # Build the client exactly as production does (its socket settings).
    monkeypatch.setattr(m.redis, "from_url", _spy)
    try:
        await m._connect_redis()
    except Exception:  # noqa: S110
        pass  # the silent server never answers the ping; only the kwargs matter
    assert seen.get("socket_timeout"), "production Redis client has no socket read timeout"
    client = real_from_url(f"redis://127.0.0.1:{port}/0", **seen)
    monkeypatch.setattr(m.app_state, "redis_client", client)
    monkeypatch.setattr(m, "_fan_baseline_restored", False)
    await house.add_zone("Bedroom", 77)
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(2026, 11, 9, 14, 0)
    try:
        await asyncio.wait_for(m.execute_active_mode(), timeout=30)
    except TimeoutError:
        raise AssertionError("execute_active_mode hung on a silent Redis") from None
    finally:
        srv.close()
