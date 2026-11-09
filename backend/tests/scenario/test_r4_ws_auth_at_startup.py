"""§9.1: HA briefly rejects the token while it (re)starts — the reconnect
loop treats auth_invalid as transient ("never give up for good"), but the
*first* connect() raises without starting the reconnect loop, and lifespan
then never assigns app_state.ha_ws.  The WebSocket (sensor ingestion,
drift correction, override re-assertion) is dead until the add-on restarts."""
import asyncio
from typing import Any

import pytest

import backend.api.dependencies as deps
import backend.api.main as main_mod
import backend.integrations as integrations
from backend.integrations import ha_websocket as hws
from backend.tests.scenario.harness import FakeHouseHA, House


class _StubSched:
    def start(self) -> None: ...
    def shutdown(self, wait: bool = True) -> None: ...


async def test_ws_dead_after_transient_auth_reject(house: House, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    async def fake_open(self: Any) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise hws.HAWebSocketAuthError("Authentication failed: invalid token")
        self._connected.set()

    async def fake_listen(self: Any) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(hws.HAWebSocketClient, "_open_and_auth", fake_open)
    monkeypatch.setattr(hws.HAWebSocketClient, "_listen_loop", fake_listen)
    shared = house.ha

    class RealHA(FakeHouseHA):
        def __init__(self, url: str = "", token: str = "", **_: Any) -> None:
            super().__init__()
            self.entities = shared.entities
            self.calls = shared.calls

    monkeypatch.setattr(integrations, "HAClient", RealHA)
    monkeypatch.setattr(main_mod, "init_scheduler", lambda: _StubSched())
    monkeypatch.setattr(main_mod.settings_instance, "home_assistant_token", "tok")
    deps._ha_client = None
    main_mod.app_state.ha_ws = None
    cm = main_mod.lifespan(main_mod.app)
    await cm.__aenter__()
    await asyncio.sleep(5)  # well past the reconnect loop's first 2 s check
    ws = main_mod.app_state.ha_ws
    print("attempts", attempts["n"], "ha_ws", ws)
    try:
        assert ws is not None and ws.connected, (
            f"one auth_invalid at startup: WebSocket never retried "
            f"(attempts={attempts['n']}, app_state.ha_ws={ws})"
        )
    finally:
        if ws is not None:
            await ws.disconnect()
