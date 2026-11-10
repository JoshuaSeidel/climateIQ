"""Add-on mode with the `api_key` option set: the browser WebSocket arrives via trusted HA ingress
(no ?api_key=) -- HAAuthMiddleware admits it, but the /ws endpoint's own settings.api_key check
closes it 4001.  Run with CLIMATEIQ_HA_ADDON_MODE=true SUPERVISOR_TOKEN=x CLIMATEIQ_API_KEY=secret."""
import asyncio
import os

import pytest


async def _ws(app, path, headers, client_ip, query=b""):
    sent = []
    q: asyncio.Queue = asyncio.Queue()
    await q.put({"type": "websocket.connect"})

    async def receive():
        return await q.get()

    async def send(m):
        sent.append(m)
        if m["type"] == "websocket.send":
            await q.put({"type": "websocket.disconnect", "code": 1000})

    scope = {"type": "websocket", "path": path, "raw_path": path.encode(), "query_string": query,
             "headers": headers, "client": (client_ip, 5555), "server": ("t", 80), "scheme": "ws",
             "subprotocols": [], "root_path": "", "asgi": {"version": "3.0"}}
    try:
        await asyncio.wait_for(app(scope, receive, send), 5)
    except Exception:  # noqa: S110
        pass
    return sent


@pytest.mark.parametrize("path", ["/ws", "/ws/zones"])
async def test_ingress_ws_with_env_key(path):
    if not (os.environ.get("CLIMATEIQ_API_KEY") and os.environ.get("SUPERVISOR_TOKEN")):
        pytest.skip("needs addon env")
    from backend.api.main import app
    sent = await _ws(app, path, [(b"x-ingress-path", b"/api/hassio_ingress/tok")], "172.30.32.2")
    types = [m["type"] for m in sent]
    assert "websocket.accept" in types and not any(m["type"] == "websocket.close" and m.get("code") == 4001 for m in sent), sent
