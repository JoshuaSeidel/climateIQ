"""Daily forecast (HA fallback when hourly unavailable): entries[:12] = 12 DAYS, not 12 h."""
from __future__ import annotations

import json
from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.test_r5_b_hourly_forecast_cold import _CapLLM, _Redis

MON = (2026, 11, 9)


async def test_daily_forecast_far_heat_wave_not_heavy_today(house: House, monkeypatch: Any) -> None:
    import backend.api.routes.chat as chat_mod

    llm = _CapLLM()

    async def _get(_db: Any) -> _CapLLM:
        return llm

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)
    daily = [{"datetime": f"2026-11-{9+i:02d}T00:00:00", "temperature": 55, "templow": 40} for i in range(12)]
    daily[9]["temperature"] = 98  # day 10: heat wave, degF
    store = {
        "weather:current": json.dumps({"data": {"temperature": 50, "temperature_unit": "°F", "state": "cloudy"}}),
        "weather:forecast": json.dumps({"data": daily}),
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
