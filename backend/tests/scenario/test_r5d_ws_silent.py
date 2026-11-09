"""§9.1: server accepts the WebSocket upgrade but never sends auth_required (HA/proxy booting).
_open_and_auth's recv() has no timeout, so connect() blocks lifespan startup forever
instead of falling back to the background reconnect loop."""
import asyncio

import websockets

from backend.integrations import ha_websocket as hws


async def test_silent_server_blocks_connect() -> None:
    release = asyncio.Event()
    async def handler(ws):
        await release.wait()
    srv = await websockets.serve(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    c = hws.HAWebSocketClient(f"http://127.0.0.1:{port}", "tok")
    task = asyncio.create_task(c.connect())
    await asyncio.sleep(15)
    hung = not task.done()
    task.cancel()
    release.set()
    srv.close()
    assert not hung, "connect() still blocked 15 s after a silent server; lifespan startup (and reconnect loop) hang forever"
