import logging

import pytest

import backend.api.main as m
from backend.api.routes.system import apply_system_mode, emergency_shutoff
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import THERMOSTAT
from backend.tests.scenario.test_r15_retry_and_shutoff import (
    MON,
    _off_schedule_applied,
    _patch_shutoff,
)


async def test_gap_unavailable_whole_gap(house, caplog):
    await _off_schedule_applied(house)
    caplog.set_level(logging.WARNING)
    house.ha.entities[THERMOSTAT].state = "unavailable"
    for mi in range(0, 12):
        house.at(*MON, 17, mi)
        await house.tick_scheduled()
    errs = [r for r in caplog.records if "giving up" in r.getMessage()]
    print("giveups", len(errs), "tries", m._gap_undo_tries, "record", m._last_mode_switch)
    assert len(errs) == 1
    house.ha.entities[THERMOSTAT].state = "off"
    for mi in range(12, 20):
        house.at(*MON, 17, mi)
        await house.tick_scheduled()
    print("after recovery mode", house.ha.mode, "record", m._last_mode_switch)


async def test_gap_giveup_stale_record_then_user_off(house):
    await _off_schedule_applied(house)
    house.ha.entities[THERMOSTAT].state = "unavailable"
    for mi in range(0, 8):
        house.at(*MON, 17, mi)
        await house.tick_scheduled()
    # comes back; user sets own off at some point
    house.ha.entities[THERMOSTAT].state = "heat"
    house.ha.set_thermostat(mode="off")
    print("record after gap giveup", m._last_mode_switch)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    print("mode after learn handover", house.ha.mode)
    assert house.ha.mode == "off", "user's own off undone via stale record left by gap give-up"


@pytest.mark.parametrize("target", ["pop", "restore", "ensure"])
async def test_shutoff_helper_raises_still_turns_off(house, monkeypatch, target):
    _patch_shutoff(house, monkeypatch)
    await house.add_zone("Living Room", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)

    async def boom(*a, **k):
        raise RuntimeError("boom")

    import backend.core.manual_override as mo

    if target == "pop":
        monkeypatch.setattr(mo, "pop_learn_undo_pending", boom)
    elif target == "restore":
        m._last_mode_switch["climate.x"] = ("off", __import__("datetime").datetime.now())
        monkeypatch.setattr(m, "_restore_mode_switch", boom)
    else:
        monkeypatch.setattr(m, "_ensure_mode_switch_loaded", boom)
    try:
        async with get_session_maker()() as db:
            await emergency_shutoff(db)
    except Exception as e:
        print("shutoff raised", e)
    assert house.ha.mode == "off", "emergency shutoff did not turn the thermostat off"


async def test_shutoff_then_scheduled_in_off_schedule_then_gap(house, monkeypatch):
    _patch_shutoff(house, monkeypatch)
    await _off_schedule_applied(house)
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    assert house.ha.mode == "off"
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    for mi in range(1, 4):
        house.at(*MON, 12, mi)
        await house.tick_all()
    for mi in range(0, 8):
        house.at(*MON, 17, mi)
        await house.tick_all()
    print("record", m._last_mode_switch)
    assert house.ha.mode == "off", "gap restored the user's emergency off"


async def test_shutoff_database_step_failure_still_turns_off(house, monkeypatch):
    """The Learn/override DB steps run before the HA off command: a failure there
    must not stop the thermostat being turned off."""
    import backend.core.manual_override as mo

    async def boom(*a, **k):
        raise RuntimeError("db hiccup")

    _patch_shutoff(house, monkeypatch)
    monkeypatch.setattr(mo, "clear_override", boom)
    await _off_schedule_applied(house)
    house.ha.set_thermostat(mode="heat")
    async with get_session_maker()() as db:
        result = await emergency_shutoff(db)
    assert house.ha.mode == "off"
    assert result["errors"], "the failed DB step should be reported"
