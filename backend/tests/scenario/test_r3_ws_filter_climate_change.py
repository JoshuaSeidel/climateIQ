"""§2 override: "If the Ecobee drops the hold itself, the override setpoint is
re-asserted" — that only happens in the WebSocket climate handler.  The WS
entity filter is built once at startup; changing climate_entities in Settings
never adds the new thermostat, so its state changes are dropped and the
override is never re-asserted (also drift detection is dead) until restart."""
import json
from typing import Any

import backend.api.main as main_mod
from backend.api.routes.settings import SystemSettingsUpdate, update_settings
from backend.core.manual_override import set_override
from backend.integrations.ha_websocket import HAWebSocketClient
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import THERMOSTAT, House, f_to_c


class _FakeSocket:
    def __init__(self, msgs: list[dict[str, Any]]) -> None:
        self._msgs = [json.dumps(m) for m in msgs]

    async def recv(self) -> str:
        if self._msgs:
            return self._msgs.pop(0)
        raise RuntimeError("socket drained")

    async def close(self) -> None: ...


async def test_override_reasserted_after_thermostat_entity_change(house: House) -> None:
    await house.add_zone("Living Room", 68)
    # At startup the configured thermostat was a different entity.
    await house.set_setting("climate_entities", "climate.old_thermostat")
    ws = HAWebSocketClient(
        url="http://fake-ha", token="t",  # noqa: S106
        entity_filter={"climate.old_thermostat", "sensor.living_room_temperature"},
        ha_temp_unit="F",
    )
    ws.add_callback(main_mod._handle_climate_state_change)
    old_ws = main_mod.app_state.ha_ws
    main_mod.app_state.ha_ws = ws
    try:
        # User picks the real thermostat in Settings.
        async with get_session_maker()() as db:
            await update_settings(SystemSettingsUpdate(climate_entities=THERMOSTAT), db)
        main_mod._primary_climate_cache = None
        house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=68)
        async with get_session_maker()() as db:
            await set_override(db, f_to_c(72), source="dashboard", duration_hours=4)

        # The Ecobee's program drops the hold → 64 °F.
        house.ha.set_thermostat(setpoint_f=64)
        st = house.ha.entities[THERMOSTAT]
        ws._ws = _FakeSocket([{  # type: ignore[assignment]
            "type": "event",
            "event": {"event_type": "state_changed", "data": {
                "entity_id": THERMOSTAT,
                "new_state": {"entity_id": THERMOSTAT, "state": st.state,
                              "attributes": dict(st.attributes)},
            }},
        }])
        await ws._listen_loop()
        import asyncio
        await asyncio.sleep(0.05)
    finally:
        main_mod.app_state.ha_ws = old_ws
    assert house.ha.setpoint_f == 72, (
        f"override (72F) not re-asserted; thermostat left at {house.ha.setpoint_f}F — "
        "the new climate entity is not in the WS filter"
    )


async def test_control_filter_contains_entity(house: House) -> None:
    await house.add_zone("Living Room", 68)
    # At startup the configured thermostat was a different entity.
    await house.set_setting("climate_entities", "climate.old_thermostat")
    ws = HAWebSocketClient(
        url="http://fake-ha", token="t",  # noqa: S106
        entity_filter={"climate.old_thermostat", "sensor.living_room_temperature", THERMOSTAT},
        ha_temp_unit="F",
    )
    ws.add_callback(main_mod._handle_climate_state_change)
    old_ws = main_mod.app_state.ha_ws
    main_mod.app_state.ha_ws = ws
    try:
        # User picks the real thermostat in Settings.
        async with get_session_maker()() as db:
            await update_settings(SystemSettingsUpdate(climate_entities=THERMOSTAT), db)
        main_mod._primary_climate_cache = None
        house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=68)
        async with get_session_maker()() as db:
            await set_override(db, f_to_c(72), source="dashboard", duration_hours=4)

        # The Ecobee's program drops the hold → 64 °F.
        house.ha.set_thermostat(setpoint_f=64)
        st = house.ha.entities[THERMOSTAT]
        ws._ws = _FakeSocket([{  # type: ignore[assignment]
            "type": "event",
            "event": {"event_type": "state_changed", "data": {
                "entity_id": THERMOSTAT,
                "new_state": {"entity_id": THERMOSTAT, "state": st.state,
                              "attributes": dict(st.attributes)},
            }},
        }])
        await ws._listen_loop()
        import asyncio
        await asyncio.sleep(0.05)
    finally:
        main_mod.app_state.ha_ws = old_ws
    assert house.ha.setpoint_f == 72, (
        f"override (72F) not re-asserted; thermostat left at {house.ha.setpoint_f}F — "
        "the new climate entity is not in the WS filter"
    )
