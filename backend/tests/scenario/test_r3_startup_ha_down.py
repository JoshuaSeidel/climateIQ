"""§9 robustness: HA not reachable when the add-on starts (host reboot — the
add-on often comes up before HA Core).  The REST client is created once in
lifespan; if connect() fails, dependencies._ha_client stays None and every
control loop returns early ("if ha_client is None: return") until some UI
request happens to hit a weather/analytics route.  The WebSocket has a
reconnect loop; the REST client has none."""
from typing import Any

import pytest

import backend.api.dependencies as deps
import backend.api.main as main_mod
import backend.integrations as integrations
from backend.integrations.ha_client import HAConnectionError
from backend.tests.scenario.harness import FakeHouseHA, House

MON = (2026, 11, 9)


class _StubWS:
    def __init__(self, *a: Any, **k: Any) -> None: ...
    def add_callback(self, cb: Any) -> None: ...
    def set_ha_temp_unit(self, u: str) -> None: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...


class _StubSched:
    def start(self) -> None: ...
    def shutdown(self, wait: bool = True) -> None: ...


async def test_loops_recover_when_ha_comes_up_after_startup(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    await house.add_zone("Living Room", 64)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=63)
    shared = house.ha
    ha_up = {"v": False}

    class RealHA(FakeHouseHA):
        def __init__(self, url: str = "", token: str = "", **_: Any) -> None:
            super().__init__()
            self.entities = shared.entities  # same simulated house
            self.calls = shared.calls

        async def connect(self) -> None:
            if not ha_up["v"]:
                raise HAConnectionError("Cannot reach Home Assistant (still booting)")

        async def get_state(self, entity_id: str) -> Any:
            if not ha_up["v"]:
                raise HAConnectionError("down")
            return await super().get_state(entity_id)

    monkeypatch.setattr(integrations, "HAClient", RealHA)
    monkeypatch.setattr(main_mod, "HAWebSocketClient", _StubWS)
    monkeypatch.setattr(main_mod, "init_scheduler", lambda: _StubSched())
    monkeypatch.setattr(main_mod.settings_instance, "home_assistant_token", "tok")
    deps._ha_client = None

    cm = main_mod.lifespan(main_mod.app)
    await cm.__aenter__()  # startup while HA is down (not exited: avoids close_db)

    ha_up["v"] = True  # HA Core finishes booting a minute later
    house.at(*MON, 12, 0)
    for i in range(3):
        house.at(*MON, 12, i)
        await house.tick_scheduled()
    assert deps._ha_client is not None, "no HA client after HA came up — loops are dead"
    assert house.ha.would_heat(), (
        f"rooms 64F, target 70F, thermostat still at {house.ha.setpoint_f}F: "
        "control loops never ran after HA came up"
    )
