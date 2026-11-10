"""The Redis socket read timeout must not kill the idle WebSocket pub/sub listener."""
import asyncio
import logging
import os
from typing import Any

import pytest

from backend.api import dependencies as dep
from backend.api.websocket import ConnectionManager


class FakeWS:
    def __init__(self) -> None:
        self.got: list[str] = []

    async def accept(self) -> None:
        return None

    async def send_text(self, t: str) -> None:
        self.got.append(t)

    async def close(self, *a: Any, **k: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_pubsub_listener_survives_idle(caplog: pytest.LogCaptureFixture) -> None:
    dep.set_shared_redis(None)
    mgr = ConnectionManager(os.environ.get("CLIMATEIQ_REDIS_URL", "redis://localhost:56379/0"))
    ws = FakeWS()
    mgr._connections.setdefault("general", set()).add(ws)  # type: ignore[arg-type]
    caplog.set_level(logging.ERROR)
    await mgr.subscribe_redis()
    await asyncio.sleep(8)  # idle > socket_timeout=3
    crashes = [r for r in caplog.records if "listener crashed" in r.message]
    assert not crashes, f"listener crashed {len(crashes)}x while idle"
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_pubsub_delivers_after_idle() -> None:
    dep.set_shared_redis(None)
    url = os.environ.get("CLIMATEIQ_REDIS_URL", "redis://localhost:56379/0")
    receiver, sender = ConnectionManager(url), ConnectionManager(url)
    ws = FakeWS()
    receiver._connections.setdefault("general", set()).add(ws)  # type: ignore[arg-type]
    await receiver.subscribe_redis()
    await asyncio.sleep(4)  # idle past the socket timeout
    await sender.broadcast({"type": "ping_test"})
    for _ in range(20):
        if ws.got:
            break
        await asyncio.sleep(0.25)
    await receiver.shutdown()
    await sender.shutdown()
    assert any("ping_test" in m for m in ws.got), ws.got
