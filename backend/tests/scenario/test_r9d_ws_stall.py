"""A stalled frontend WebSocket client blocks fan-out to every other client."""
import asyncio

from backend.api.websocket import ConnectionManager


class Stalled:
    async def accept(self): ...
    async def send_text(self, _): await asyncio.sleep(3600)
    async def close(self): ...


class Good:
    def __init__(self): self.got = []
    async def accept(self): ...
    async def send_text(self, p): self.got.append(p)
    async def close(self): ...


async def test_stalled_client_blocks_others():
    mgr = ConnectionManager("redis://localhost:1/0")
    s = Stalled()
    goods = [Good() for _ in range(20)]
    await mgr.connect(s, "general")
    for g in goods:
        await mgr.connect(g, "general")
    try:
        await asyncio.wait_for(mgr._send_local({"type": "x"}), timeout=3)
    except TimeoutError:
        pass
    assert all(g.got for g in goods), "healthy client received nothing: one stalled client stalls all broadcasts (and the ZoneManager feed that follows broadcast in _ingest_change_for_sensor)"
