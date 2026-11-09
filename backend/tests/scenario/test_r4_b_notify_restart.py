"""Probe §8: notification throttle ('already notified') must survive restarts."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _Notif

MON = (2026, 11, 9)


def _restart() -> None:
    main_mod._active_mode_last_notify.clear()
    main_mod._active_mode_state.clear()
    main_mod._runtime_state_loaded = False
    main_mod._fan_baseline_restored = False


async def _run(house: House, monkeypatch: Any, mode: SystemMode, restart: bool) -> list:
    import backend.api.routes.chat as chat_mod

    async def _down(_db: Any) -> Any:
        raise RuntimeError("no llm")

    monkeypatch.setattr(chat_mod, "get_llm_provider", _down)
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.add_zone("Bedroom", 74, comfort_preferences={"temp_min": 21, "temp_max": 23})
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(mode)
    house.ha.set_thermostat(mode="cool", setpoint_f=74, reading_f=74)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    sp1 = house.ha.setpoint_f
    if restart:
        _restart()
    house.ha.set_thermostat(setpoint_f=76)  # Ecobee drifted; tick re-asserts the same 70
    house.at(*MON, 14, 10)
    await main_mod.execute_active_mode()
    return [sp1, house.ha.setpoint_f, n.sent]


async def test_control_no_restart(house: House, monkeypatch: Any) -> None:
    sp1, sp2, sent = await _run(house, monkeypatch, SystemMode.active, restart=False)
    assert sp1 == sp2 and len(sent) == 1, (sp1, sp2, sent)


async def test_restart_resends_ai_notification(house: House, monkeypatch: Any) -> None:
    sp1, sp2, sent = await _run(house, monkeypatch, SystemMode.active, restart=True)
    assert sp1 == sp2
    assert len(sent) == 1, f"setpoint {sp1}→{sp2} (same value re-asserted 10 min later) re-notified after restart: {sent}"
