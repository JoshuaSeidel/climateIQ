"""Probe §6.4: fans changed by hand are left alone; override state survives restarts."""
from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _install_stepped_fan, _use_llm

MON = (2026, 11, 9)


async def test_hand_changed_fan_retaken_after_restart(house: House, monkeypatch: Any) -> None:
    r = aioredis.from_url("redis://localhost:56379/12")
    await r.delete(main_mod._FAN_BASELINE_REDIS_KEY)
    monkeypatch.setattr(main_mod.app_state, "redis_client", r)
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS: Bedroom=100\nREASON: cool")
    await house.add_zone("Bedroom", 75, allow_fan_control=True, fan_entities=["fan.bedroom"])
    writes = _install_stepped_fan(house, "fan.bedroom", speeds=3)
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert writes == [100]
    # User turns the fan down to low by hand.
    house.ha.entities["fan.bedroom"] = EntityState(
        entity_id="fan.bedroom", state="on", attributes={"percentage": 33})
    house.set_room("Bedroom", 74.5)
    house.at(*MON, 14, 10)
    await main_mod.execute_active_mode()
    assert "fan.bedroom" in main_mod._fan_user_owned and writes == [100]
    # Add-on restart.
    main_mod._fan_baseline.clear()
    main_mod._fan_user_owned.clear()
    main_mod._fan_baseline_restored = False
    main_mod._active_mode_state.clear()
    house.set_room("Bedroom", 75)
    house.at(*MON, 14, 20)
    await main_mod.execute_active_mode()
    await r.aclose()
    assert writes == [100], f"hand-set fan re-taken 20 min later after a restart: writes={writes}"
