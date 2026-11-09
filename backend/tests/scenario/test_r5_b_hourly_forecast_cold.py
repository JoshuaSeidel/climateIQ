"""Probe §6 (AI prompt / heavy-day preconditioning): an hourly forecast (HA's normal
weather.get_forecasts shape) carries only `temperature`, no `templow`.  The AI tick takes
the forecast *low* only from `templow`, so a 12 h run of 15-25 degF hours never raises
HEAVY_COLD_DAY and the prompt shows no forecast low at all."""
from __future__ import annotations

import json
from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


class _Redis:
    def __init__(self, store: dict[str, str]) -> None:
        self.store = store

    async def get(self, k: str) -> str | None:
        return self.store.get(k)

    async def set(self, k: str, v: Any, *a: Any, **kw: Any) -> None:
        self.store[k] = v

    setex = set


class _CapLLM:
    provider = "ollama"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, **kw: Any) -> dict[str, Any]:
        self.prompts.append(kw["user_prompt"])
        return {"content": "RECOMMENDED_TEMP: 72\nREASON: x"}


async def test_hourly_forecast_flags_heavy_cold_day(house: House, monkeypatch: Any) -> None:
    import backend.api.routes.chat as chat_mod

    llm = _CapLLM()

    async def _get(_db: Any) -> _CapLLM:
        return llm

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)
    hourly = [{"temperature": 15 + i % 10, "condition": "snowy"} for i in range(24)]  # degF, no templow
    store = {
        "weather:current": json.dumps({"data": {"temperature": 20, "temperature_unit": "°F", "state": "snowy"}}),
        "weather:forecast": json.dumps({"data": hourly}),
    }
    monkeypatch.setattr(main_mod.app_state, "redis_client", _Redis(store))
    await house.add_zone("Bedroom", 68)
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=68)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert llm.prompts, "LLM not called"
    assert "HEAVY_COLD_DAY" in llm.prompts[0], llm.prompts[0]
