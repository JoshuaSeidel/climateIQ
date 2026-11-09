"""Scenario tests: override lifecycle, cooling mirror, windows, AI guard (docs/CONTROL_SPEC.md)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def _override(house: House, temp_f: float) -> None:
    from backend.api.routes.system import set_manual_override
    from backend.models.database import get_session_maker

    async with get_session_maker()() as db:
        await set_manual_override({"temperature": temp_f}, db)


async def test_override_expires_at_next_schedule_start(house: House) -> None:
    """Spec §2 — override lasts until the next boundary, then the schedule applies."""
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    await house.add_schedule("Evening", "18:00", "22:00", 66, ["Living Room"], priority=6)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 10, 0)
    await _override(house, 76)
    house.at(*MON, 17, 59)
    await house.tick_scheduled()
    assert house.ha.setpoint_f == 76
    house.at(*MON, 18, 0)
    await house.tick_scheduled()
    assert not house.ha.would_heat(), f"still heating to {house.ha.setpoint_f} after override expired"


async def test_resume_clears_override(house: House) -> None:
    from backend.api.routes.system import QuickActionRequest, execute_quick_action
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 10, 0)
    await _override(house, 76)
    async with get_session_maker()() as db:
        result = await execute_quick_action(QuickActionRequest(action="resume"), db)
    assert result.success
    house.at(*MON, 10, 1)
    await house.tick_scheduled()
    assert not house.ha.would_heat(), f"setpoint {house.ha.setpoint_f} — override not cleared"


async def test_cool_mode_never_cools_rooms_below_target(house: House) -> None:
    """Spec §5.3 mirror."""
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="cool", setpoint_f=70, reading_f=74)
    house.at(*MON, 14, 0)
    await house.tick_scheduled()
    assert not house.ha.would_cool(), f"cooling to {house.ha.setpoint_f} with rooms at 70 < 72"


async def test_hot_rooms_get_cooling(house: House) -> None:
    """Spec §5.4 mirror."""
    await house.add_zone("Living Room", 76)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="cool", setpoint_f=76, reading_f=73)
    house.at(*MON, 14, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "cool"
    assert house.ha.would_cool(), f"setpoint {house.ha.setpoint_f} vs reading 73"


async def test_thermostat_in_heat_cool_is_moved_to_explicit_mode(house: House) -> None:
    """Spec §4.3."""
    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat_cool", low_f=64, high_f=78, reading_f=67)
    house.at(*MON, 14, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "heat"
    assert house.ha.would_heat()


async def test_time_window_blocking_heat_parks_setpoint(house: House) -> None:
    """Spec §5.5 — even when the thermostat already sits at the computed setpoint."""
    await house.add_zone("Living Room", 69.5)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    await house.set_setting(
        "hvac_time_windows",
        {"enabled": True, "windows": [{
            "name": "No daytime heat", "start_time": "08:00", "end_time": "20:00",
            "days_of_week": [], "allow_heat": False, "allow_cool": True,
        }]},
    )
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=68)
    house.at(*MON, 9, 0)
    await house.tick_scheduled()
    assert not house.ha.would_heat(), f"heat runs inside a no-heat window (setpoint {house.ha.setpoint_f})"


async def test_warmup_allowed_in_a_gap(house: House) -> None:
    """Spec §7 — no other schedule active, heat mode, cold rooms → start early."""
    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=66)

    class _ZM:  # zone_manager stand-in: preconditioning only checks existence
        def get_state(self, _zid: Any) -> object:
            return object()

    import backend.api.main as main_mod

    old = main_mod.app_state.zone_manager
    main_mod.app_state.zone_manager = _ZM()  # type: ignore[assignment]
    try:
        house.at(*MON, 6, 20)
        await house.tick_scheduled()
    finally:
        main_mod.app_state.zone_manager = old
    assert house.ha.would_heat(), "no warm-up in a gap before Daytime"


class _FakeLLM:
    provider = "ollama"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def generate(self, **_kw: Any) -> dict[str, Any]:
        return {"content": self.answer}

    async def chat(self, **_kw: Any) -> dict[str, Any]:
        return {"content": self.answer}


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> _FakeLLM:
    fake = _FakeLLM("RECOMMENDED_TEMP: 80\nFAN_ACTIONS:\nREASON: make it toasty")

    async def _get(_db: Any) -> _FakeLLM:
        return fake

    import backend.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)
    return fake


async def test_ai_mode_cannot_heat_rooms_past_schedule_target(house: House, llm: _FakeLLM) -> None:
    """Spec §6.1 — the AI asks for 80°F at bedtime; the schedule guard wins."""
    await house.add_zone("Bedroom", 73)
    await house.add_schedule("Night", "20:00", "06:30", 69, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=70)
    house.at(*MON, 22, 0)
    await house.tick_all()
    assert not house.ha.would_heat(), f"AI heated a 73°F bedroom to {house.ha.setpoint_f}"


async def test_ai_mode_respects_manual_override(house: House, llm: _FakeLLM) -> None:
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 10, 0)
    await _override(house, 75)
    llm.answer = "RECOMMENDED_TEMP: 66\nFAN_ACTIONS:\nREASON: save energy"
    house.at(*MON, 10, 10)
    await house.tick_all()
    assert house.ha.setpoint_f == 75


async def test_follow_me_occupied_cold_room_does_not_override_bedtime(house: House) -> None:
    """Spec §2 — Follow-Me defers to an active schedule even if another room is cold."""
    await house.add_zone("Bedroom", 74)
    await house.add_zone("Living Room", 64, comfort_preferences={"temp_min": 21, "temp_max": 23})
    await house.add_schedule("Night", "20:00", "06:30", 69, ["Bedroom"])
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=70)
    house.at(*MON, 23, 0)
    await house.tick_all()
    assert not house.ha.would_heat(), f"Follow-Me heated during bedtime (setpoint {house.ha.setpoint_f})"
