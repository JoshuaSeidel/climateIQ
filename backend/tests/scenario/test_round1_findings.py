"""Scenario regressions for review round 1 (docs/CONTROL_SPEC.md)."""

from __future__ import annotations

from typing import Any

import pytest

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


class _ZM:
    def get_state(self, _zid: Any) -> object:
        return object()


@pytest.fixture
def zone_manager() -> Any:
    old = main_mod.app_state.zone_manager
    main_mod.app_state.zone_manager = _ZM()  # type: ignore[assignment]
    yield
    main_mod.app_state.zone_manager = old


async def test_schedule_runner_never_writes(house: House) -> None:
    """Spec §2 — only maintenance/AI write; two schedules starting together."""
    await house.add_zone("Living Room", 70)
    await house.add_schedule("A", "07:00", "12:00", 68, ["Living Room"], priority=5)
    await house.add_schedule("B", "07:00", "12:00", 74, ["Living Room"], priority=1)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 7, 0)
    await main_mod.execute_schedules()
    assert house.ha.writes() == []
    await main_mod.maintain_climate_offset()
    assert not house.ha.would_heat(), "lower-priority 74°F schedule won"


async def test_off_schedule_is_not_preconditioned(house: House, zone_manager: Any) -> None:
    await house.add_zone("Living Room", 64)
    await house.add_schedule("Away", "09:00", "17:00", 68, hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=65)
    house.at(*MON, 8, 50)
    await house.tick_scheduled()
    assert house.ha.mode == "heat", "thermostat switched off before the off-schedule"


async def test_all_zones_schedule_preconditions(house: House, zone_manager: Any) -> None:
    await house.add_zone("Living Room", 66)
    await house.add_zone("Kitchen", 66)
    await house.add_schedule("Day", "06:30", "18:00", 70)  # all zones
    house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=66)
    house.at(*MON, 6, 20)
    await house.tick_scheduled()
    assert house.ha.would_heat()


async def test_precondition_across_midnight(house: House, zone_manager: Any) -> None:
    await house.add_zone("Bedroom", 75)
    await house.add_schedule("Night cool", "00:10", "06:00", 68, ["Bedroom"], days=[1])
    house.ha.set_thermostat(mode="cool", setpoint_f=78, reading_f=75)
    house.at(*MON, 23, 59)
    await house.tick_scheduled()
    assert house.ha.would_cool(), "no cool-down before a 00:10 schedule"


async def test_follow_me_single_owner_while_preconditioning(house: House, zone_manager: Any) -> None:
    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=66)
    house.at(*MON, 6, 20)
    await house.tick_all()
    values = {round(p.get("temperature", 0)) for d, sv, p in house.ha.writes() if sv == "set_temperature"}
    assert len(values) <= 1, f"two owners wrote {values}"


async def test_active_mode_llm_down_still_stops_heating_warm_rooms(house: House, monkeypatch: Any) -> None:
    """Spec §6.1 — the schedule binds even without an LLM."""
    import backend.api.routes.chat as chat_mod

    async def _no_llm(_db: Any) -> Any:
        raise RuntimeError("ollama down")

    monkeypatch.setattr(chat_mod, "get_llm_provider", _no_llm)
    await house.add_zone("Bedroom", 74)
    await house.add_schedule("Night", "20:00", "06:30", 69, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=77, reading_f=71)
    house.at(*MON, 22, 0)
    await house.tick_all()
    assert not house.ha.would_heat(), f"setpoint {house.ha.setpoint_f} with LLM down"


async def test_active_steady_state_still_corrects_stale_setpoint(house: House, monkeypatch: Any) -> None:
    import backend.api.routes.chat as chat_mod

    async def _no_llm(_db: Any) -> Any:
        raise RuntimeError("not needed")

    monkeypatch.setattr(chat_mod, "get_llm_provider", _no_llm)
    await house.add_zone("Living Room", 70.2)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=79, reading_f=70)
    house.at(*MON, 12, 0)
    await house.tick_all()
    assert not house.ha.would_heat(), f"stale 79°F kept at steady state ({house.ha.setpoint_f})"


async def test_manual_override_switches_cool_to_heat(house: House) -> None:
    from backend.api.routes.system import set_manual_override
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 64)
    house.ha.set_thermostat(mode="cool", setpoint_f=76, reading_f=65)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await set_manual_override({"temperature": 70}, db)
    assert house.ha.mode == "heat"
    assert house.ha.would_heat()


async def test_override_out_of_range_is_rejected(house: House) -> None:
    from fastapi import HTTPException

    from backend.api.routes.system import set_manual_override
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 70)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        with pytest.raises(HTTPException):
            await set_manual_override({"temperature": 22}, db)  # 22°F
    assert house.ha.writes() == []


async def test_resume_in_learn_does_not_hold(house: House) -> None:
    from backend.api.routes.system import QuickActionRequest, execute_quick_action
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    await house.set_mode(SystemMode.learn)
    house.at(*MON, 8, 0)
    async with get_session_maker()() as db:
        res = await execute_quick_action(QuickActionRequest(action="resume"), db)
    assert res.success
    assert not [c for c in house.ha.writes() if c[1] == "set_temperature"]


async def test_no_mode_flapping_inside_deadband(house: House) -> None:
    await house.add_zone("Living Room", 70.1)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=70)
    for i, temp in enumerate([70.1, 69.9, 70.1, 69.9]):
        house.set_room("Living Room", temp)
        house.at(*MON, 12, i)
        await house.tick_scheduled()
    assert not [c for c in house.ha.writes() if c[1] == "set_hvac_mode"]


async def test_heat_cool_blocked_direction_parks_sides(house: House) -> None:
    await house.add_zone("Living Room", 76)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    await house.set_setting(
        "hvac_time_windows",
        {"enabled": True, "windows": [{
            "name": "No cool", "start_time": "08:00", "end_time": "20:00",
            "days_of_week": [], "allow_heat": True, "allow_cool": False,
        }]},
    )
    house.ha.set_thermostat(mode="heat_cool", low_f=68, high_f=74, reading_f=75)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert not house.ha.would_cool(), "cool side still below the reading in a no-cool window"


async def test_emergency_shutoff_turns_off_and_blocks_writes(house: House) -> None:
    from backend.api.routes.system import emergency_shutoff
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 60)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=60)
    house.at(*MON, 12, 0)
    main_mod.settings_instance.home_assistant_token = ""  # shutoff path uses its own client
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    await house.tick_all()
    assert house.ha.mode != "heat" or not [
        c for c in house.ha.writes() if c[1] == "set_temperature"
    ]
