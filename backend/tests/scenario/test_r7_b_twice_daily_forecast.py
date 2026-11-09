"""Forecast 'next 12 h' window: the daily-detect heuristic needs >=20 h spacing, so a twice-daily
(12 h spaced, e.g. NWS/Met Office day-night periods) forecast is treated as hourly and 12 entries = 6 DAYS."""
from __future__ import annotations

import json
from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.test_r5_b_hourly_forecast_cold import _CapLLM, _Redis

MON = (2026, 11, 9)


async def test_twice_daily_far_heat_wave_not_heavy_today(house: House, monkeypatch: Any) -> None:
    import backend.api.routes.chat as chat_mod

    llm = _CapLLM()

    async def _get(_db: Any) -> _CapLLM:
        return llm

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)
    ents = []
    for i in range(12):
        d, h = 9 + i // 2, 6 if i % 2 == 0 else 18
        ents.append({"datetime": f"2026-11-{d:02d}T{h:02d}:00:00+00:00", "temperature": 55, "templow": 40})
    ents[8]["temperature"] = 98  # day 5 (Nov 13)
    store = {
        "weather:current": json.dumps({"data": {"temperature": 50, "temperature_unit": "°F", "state": "cloudy"}}),
        "weather:forecast": json.dumps({"data": ents}),
    }
    monkeypatch.setattr(main_mod.app_state, "redis_client", _Redis(store))
    await house.add_zone("Bedroom", 68)
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=68)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert llm.prompts, "LLM not called"
    assert "HEAVY_HOT_DAY" not in llm.prompts[0], llm.prompts[0]
