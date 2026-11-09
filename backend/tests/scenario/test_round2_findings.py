"""Scenario regressions for review round 2 (docs/CONTROL_SPEC.md)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import backend.api.main as main_mod
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_must_run_never_exceeds_max_offset(house: House) -> None:
    """Spec §5.2 — a room that never warms must not walk the setpoint up."""
    await house.add_zone("Bedroom", 64)  # stuck cold (closed vent / frozen sensor)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    for i, reading in enumerate([70, 74, 77, 80, 84, 88]):
        house.ha.set_thermostat(reading_f=reading)
        house.at(*MON, 12, i)
        await house.tick_scheduled()
        assert (house.ha.setpoint_f or 0) <= 78.01, f"{house.ha.setpoint_f} at reading {reading}"


async def test_room_one_degree_below_target_gets_heat(house: House) -> None:
    await house.add_zone("Living Room", 69)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.would_heat()


async def test_heat_cool_park_is_idempotent(house: House) -> None:
    await house.add_zone("Living Room", 76)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    await house.set_setting("hvac_time_windows", {"enabled": True, "windows": [{
        "name": "No cool", "start_time": "08:00", "end_time": "20:00",
        "days_of_week": [], "allow_heat": True, "allow_cool": False,
    }]})
    house.ha.set_thermostat(mode="heat_cool", low_f=68, high_f=74, reading_f=75)
    for i in range(3):
        house.at(*MON, 12, i)
        await house.tick_scheduled()
    parks = [c for c in house.ha.writes() if c[1] == "set_temperature"]
    assert len(parks) <= 1, parks
    assert not house.ha.would_cool()


async def test_heat_lock_does_not_switch_hot_house_to_heat(house: House) -> None:
    """Spec §4.4 — locks only forbid; they never pick a direction the rooms don't need."""
    await house.add_zone("Living Room", 78)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    await house.set_setting("hvac_control_mode", "heat")
    house.ha.set_thermostat(mode="cool", setpoint_f=76, reading_f=77)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert not [c for c in house.ha.writes() if c[1] == "set_hvac_mode"]
    assert not house.ha.would_cool(), "cool runs although the heat lock forbids it"


async def test_follow_me_nobody_home_never_flips_direction(house: House) -> None:
    """Spec §6b.2 — the setback must not switch heat→cool (and back)."""
    await house.add_zone("Living Room", 72, comfort_preferences={"temp_min": 21, "temp_max": 23})
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=72)

    async def _vacant(_zid: Any, _db: Any) -> bool:
        return False

    import pytest as _pt

    mp = _pt.MonkeyPatch()
    mp.setattr(main_mod, "infer_zone_occupancy", _vacant)
    try:
        for i in range(4):
            house.at(*MON, 13, i * 2)
            await main_mod.execute_follow_me_mode()
    finally:
        mp.undo()
    assert not [c for c in house.ha.writes() if c[1] == "set_hvac_mode"]
    assert house.ha.mode == "heat"


async def test_active_ignores_unrelated_rooms_when_schedule_zones_excluded(
    house: House, monkeypatch: Any
) -> None:
    import backend.api.routes.chat as chat_mod

    async def _no_llm(_db: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(chat_mod, "get_llm_provider", _no_llm)
    await house.add_zone("Nursery", 66, exclude_from_metrics=True)
    await house.add_zone("Living Room", 75)
    await house.add_schedule("Nap", "12:00", "15:00", 70, ["Nursery"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=72)
    house.at(*MON, 13, 0)
    await house.tick_all()
    assert not house.ha.would_cool()
    assert not [c for c in house.ha.writes() if c[1] == "set_hvac_mode"]


async def test_dashboard_set_matches_preconditioning(house: House) -> None:
    from backend.api.routes.system import get_override_status

    class _ZM:
        def get_state(self, _z: Any) -> object:
            return object()

    old = main_mod.app_state.zone_manager
    main_mod.app_state.zone_manager = _ZM()  # type: ignore[assignment]
    try:
        await house.add_zone("Living Room", 66)
        await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
        house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=66)
        house.at(*MON, 6, 20)
        await house.tick_scheduled()
        async with get_session_maker()() as db:
            status = await get_override_status(db)
    finally:
        main_mod.app_state.zone_manager = old
    assert status.get("schedule_target_temp") == pytest.approx(72, abs=0.6)


async def test_override_duration_capped_at_24h(house: House) -> None:
    from backend.core.manual_override import set_override

    await house.add_zone("Living Room", 70)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        expires = await set_override(db, 21.0, source="t", duration_hours=500)
    assert (expires - datetime(2026, 11, 9, 17, 0, tzinfo=ZoneInfo("UTC"))).total_seconds() <= 24 * 3600 + 1


async def test_override_expiry_follows_newly_added_schedule(house: House) -> None:
    from backend.core.manual_override import get_active_override, set_override

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Evening", "18:00", "22:00", 68, ["Living Room"])
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await set_override(db, 23.0, source="t")
    await house.add_schedule("Afternoon", "14:00", "16:00", 70, ["Living Room"])
    house.at(*MON, 14, 30)
    async with get_session_maker()() as db:
        assert await get_active_override(db) is None


async def test_expired_cleanup_never_deletes_a_new_override(house: House) -> None:
    from backend.core.manual_override import clear_override, get_active_override, set_override

    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await set_override(db, 22.0, source="new")
        await clear_override(db, reason="expired", expected_set_at="2000-01-01T00:00:00+00:00")
        assert await get_active_override(db) is not None


async def test_shutoff_wins_over_inflight_tick(house: House, monkeypatch: Any) -> None:
    """Spec §2 — a tick that already decided must not turn the HVAC back on."""
    from backend.api.routes.system import apply_system_mode

    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="cool", setpoint_f=76, reading_f=66)
    house.at(*MON, 12, 0)

    real_select = main_mod._auto_select_hvac_mode
    parked, release = asyncio.Event(), asyncio.Event()

    async def _slow_select(*a: Any, **k: Any) -> Any:
        res = await real_select(*a, **k)
        parked.set()
        await release.wait()  # decision made, write not yet attempted
        return res

    monkeypatch.setattr(main_mod, "_auto_select_hvac_mode", _slow_select)
    tick = asyncio.create_task(main_mod.maintain_climate_offset())
    await asyncio.wait_for(parked.wait(), 5)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    release.set()
    await tick
    heats = [p for _, s, p in house.ha.writes() if s == "set_hvac_mode" and p.get("hvac_mode") == "heat"]
    assert not heats, "automatic heat switch landed after Learn"
    assert not [c for c in house.ha.writes() if c[1] == "set_temperature"]


async def test_manual_override_survives_inflight_tick(house: House, monkeypatch: Any) -> None:
    from backend.core.manual_control import apply_manual_setpoint

    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:00", "22:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=66)
    house.at(*MON, 12, 0)

    real_select = main_mod._auto_select_hvac_mode
    parked, release = asyncio.Event(), asyncio.Event()

    async def _slow_select(*a: Any, **k: Any) -> Any:
        res = await real_select(*a, **k)
        if not parked.is_set():
            parked.set()
            await release.wait()
        return res

    monkeypatch.setattr(main_mod, "_auto_select_hvac_mode", _slow_select)
    tick = asyncio.create_task(main_mod.maintain_climate_offset())
    await asyncio.wait_for(parked.wait(), 5)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 62, source="dial")
    release.set()
    await tick
    assert house.ha.setpoint_f == 62
    assert house.ha.mode == "cool"


def test_fall_back_repeated_hour_keeps_occurrence() -> None:
    from backend.core.schedule_guard import active_occurrence

    tz = ZoneInfo("America/New_York")
    # 2026-11-01 01:05 EST (second pass of the 1 am hour)
    assert active_occurrence([6], "01:15", "08:00", datetime(2026, 11, 1, 1, 5, tzinfo=tz, fold=1))


async def test_chat_command_uses_display_unit(house: House) -> None:
    from backend.api.routes.chat import _command_set_zone_temp

    await house.add_zone("Living Room", 20)
    await house.set_setting("temperature_unit", "C")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        err = await _command_set_zone_temp(db, house.zones["Living Room"], 21.0, "chat")
    assert err is None
    assert house.ha.setpoint_f == pytest.approx(69.8, abs=0.11)


class _Notif:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_ha_notification(self, title: str, message: str, target: Any = None, **_k: Any) -> None:
        self.sent.append((title, message))


class _LLM:
    provider = "ollama"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def generate(self, **_kw: Any) -> dict[str, Any]:
        return {"content": self.answer}

    async def chat(self, **_kw: Any) -> dict[str, Any]:
        return {"content": self.answer}


def _use_llm(monkeypatch: Any, answer: str) -> None:
    import backend.api.routes.chat as chat_mod

    llm = _LLM(answer)

    async def _get(_db: Any) -> _LLM:
        return llm

    monkeypatch.setattr(chat_mod, "get_llm_provider", _get)


async def test_restart_keeps_schedule_notification_and_preconditioning(house: House) -> None:
    """Spec §7.4/§8 — runtime state survives an add-on restart."""
    from sqlalchemy import select

    from backend.core.schedule_guard import governing_schedule
    from backend.models.database import Schedule

    class _ZM:
        def get_state(self, _z: Any) -> object:
            return object()

    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    old = main_mod.app_state.zone_manager
    main_mod.app_state.zone_manager = _ZM()  # type: ignore[assignment]
    try:
        await house.add_zone("Living Room", 66)
        await house.add_zone("Bedroom", 69)
        await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
        await house.add_schedule("Sleeping", "20:00", "06:00", 69, ["Bedroom"])
        house.ha.set_thermostat(mode="heat", setpoint_f=62, reading_f=66)
        house.at(*MON, 6, 20)
        await house.tick_scheduled()
        assert main_mod._preconditioning
        # Restart: all in-memory state lost.
        main_mod._preconditioning.clear()
        main_mod._last_executed_schedules = set()
        main_mod._runtime_state_loaded = False
        house.at(*MON, 6, 22)
        await main_mod.execute_schedules()
        async with get_session_maker()() as db:
            scheds = list((await db.execute(select(Schedule))).scalars().all())
        g, _ = governing_schedule(scheds, datetime.now(ZoneInfo("America/New_York")), main_mod._preconditioning)
        assert g is not None and g.name == "Day"

        house.at(*MON, 20, 0)
        await main_mod.execute_schedules()
        main_mod._last_executed_schedules = set()
        main_mod._runtime_state_loaded = False
        house.at(*MON, 23, 0)
        await main_mod.execute_schedules()
        assert [t for t, _ in n.sent].count("Schedule Activated: Sleeping") == 1, n.sent
    finally:
        main_mod.app_state.zone_manager = old


async def test_schedule_notification_uses_display_unit(house: House) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.set_setting("temperature_unit", "C")
    await house.add_zone("Bedroom", 69)
    await house.add_schedule("Sleeping", "20:00", "06:30", 69, ["Bedroom"])
    house.at(*MON, 20, 0)
    await main_mod.execute_schedules()
    assert n.sent and "°C" in n.sent[0][1], n.sent


async def test_spring_forward_gap_start_fires_at_transition(house: House) -> None:
    from backend.core.manual_override import set_override

    await house.add_zone("Bedroom", 69)
    await house.add_schedule("Early", "02:30", "08:00", 72, ["Bedroom"], days=[6])
    house.at(2027, 3, 14, 1, 0)
    async with get_session_maker()() as db:
        await set_override(db, 21.0, source="t")
    import time_machine

    house.stop_clock()
    house._traveller = time_machine.travel(datetime(2027, 3, 14, 7, 10, tzinfo=ZoneInfo("UTC")), tick=False)
    house._traveller.start()  # 03:10 EDT
    async with get_session_maker()() as db:
        assert not await main_mod._manual_override_active(db)


async def test_ai_switch_sends_one_notification(house: House, monkeypatch: Any) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nFAN_ACTIONS:\nREASON: cool down")
    await house.add_zone("Living Room", 76)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=74, reading_f=75)
    house.at(*MON, 14, 0)
    await main_mod.execute_active_mode()
    assert house.ha.mode == "cool"
    assert len(n.sent) == 1, n.sent


async def test_freeform_unbanded_cold_room_does_not_drive_heat(house: House, monkeypatch: Any) -> None:
    """Spec §6.2 — a cold room with no comfort band doesn't heat banded rooms past their band."""
    _use_llm(monkeypatch, "")
    await house.add_zone("Living Room", 73, comfort_preferences={"temp_min": 21, "temp_max": 23})
    await house.add_zone("Basement", 58)
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=72)
    house.at(*MON, 12, 0)
    await main_mod.execute_active_mode()
    assert not house.ha.would_heat(), house.ha.setpoint_f
