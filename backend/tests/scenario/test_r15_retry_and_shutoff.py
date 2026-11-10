"""Bounded off-restore retries (gap + Learn) and emergency shutoff with a failing HA call."""

import logging
from typing import Any

import pytest

import backend.api.main as main_mod
import backend.integrations.ha_client as hc
from backend.api.routes.system import apply_system_mode, emergency_shutoff
from backend.config import get_settings
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


def _fail_next_hvac(house: House, mode: str) -> dict[str, int]:
    """Make the next ``fail["n"]`` set_hvac_mode(mode) calls raise."""
    fail = {"n": 0}
    real = house.ha.call_service

    async def cs(domain: str, service: str, data: Any = None, target: Any = None, **kw: Any):
        if fail["n"] > 0 and service == "set_hvac_mode" and (data or {}).get("hvac_mode") == mode:
            fail["n"] -= 1
            raise RuntimeError("transient HA failure")
        return await real(domain, service, data, target, **kw)

    house.ha.call_service = cs  # type: ignore[method-assign]
    return fail


def _patch_shutoff(house: House, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "home_assistant_token", "tok", raising=False)

    class _Ctx:
        async def __aenter__(self_inner) -> Any:
            return house.ha

        async def __aexit__(self_inner, *a: Any) -> bool:
            return False

    monkeypatch.setattr(hc, "HAClient", lambda *a, **k: _Ctx())


async def _off_schedule_applied(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"


def _resumes(house: House) -> int:
    return len([c for c in house.ha.calls if c[1] == "resume_program"])


def _heat_cool_attempts(house: House) -> int:
    return len(
        [
            c
            for c in house.ha.calls
            if c[1] in ("set_hvac_mode", "FAILED_set_hvac_mode")
            and c[2].get("hvac_mode") == "heat_cool"
        ]
    )


@pytest.mark.parametrize("failures", [1, 3, 5, 8])
async def test_gap_restore_retry_is_bounded_and_resume_is_one_shot(
    house: House, caplog: pytest.LogCaptureFixture, failures: int
) -> None:
    await _off_schedule_applied(house)
    fail = _fail_next_hvac(house, "heat_cool")
    fail["n"] = failures
    before = _resumes(house)
    for h, m in [(17, 0)] + [(17, i) for i in range(1, 16)]:
        house.at(*MON, h, m)
        await house.tick_scheduled()
    assert _resumes(house) - before == 1, "the Ecobee resume must be sent exactly once"
    gave_up = [r for r in caplog.records if "giving up" in r.getMessage()]
    if failures <= 5:
        assert house.ha.mode != "off", "a recoverable failure must not leave the house off"
        assert not gave_up
    else:
        assert house.ha.mode == "off"
        assert len(gave_up) == 1


@pytest.mark.parametrize("failures", [1, 3, 5, 8])
async def test_learn_restore_retry_is_bounded_and_resume_is_one_shot(
    house: House, caplog: pytest.LogCaptureFixture, failures: int
) -> None:
    await _off_schedule_applied(house)
    fail = _fail_next_hvac(house, "heat_cool")
    fail["n"] = failures
    before = _resumes(house)
    caplog.set_level(logging.WARNING)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    for m in range(1, 16):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert _resumes(house) - before == 1
    gave_up = [r for r in caplog.records if "giving up" in r.getMessage()]
    if failures <= 5:
        assert house.ha.mode != "off"
    else:
        assert house.ha.mode == "off"
        assert len(gave_up) == 1


async def test_shutoff_with_failing_off_command_still_forgets_the_record(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The emergency shutoff is the user's intent: even if HA rejects its off command,
    a later handback must not treat the user's off as ClimateIQ's."""
    _patch_shutoff(house, monkeypatch)
    await _off_schedule_applied(house)
    fail = _fail_next_hvac(house, "off")
    fail["n"] = 1
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    assert all(m != "off" for m, _t in main_mod._last_mode_switch.values())
    # The user turns heat on, then off again by hand; switch to Scheduled into a gap.
    house.ha.set_thermostat(mode="heat")
    house.ha.set_thermostat(mode="off")
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    for m in range(0, 6):
        house.at(*MON, 17, m)
        await house.tick_all()
    assert house.ha.mode == "off", "ClimateIQ undid the user's own off using a stale record"


async def test_shutoff_cancels_pending_learn_restore(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_shutoff(house, monkeypatch)
    await _off_schedule_applied(house)
    fail = _fail_next_hvac(house, "heat_cool")
    fail["n"] = 6
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)  # restore fails -> retry pending
    fail["n"] = 0
    off_fail = _fail_next_hvac(house, "off")
    off_fail["n"] = 1
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    for m in range(1, 11):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert house.ha.mode == "off", "heating came back after the emergency shutoff"


async def test_leaving_learn_with_pending_restore_keeps_off_schedule_ownership(
    house: House,
) -> None:
    """Learn handover's restore fails, then the user goes back to Scheduled while the
    off schedule still governs: the off is ClimateIQ's again, so the gap handback
    must still restore it."""
    await _off_schedule_applied(house)
    fail = _fail_next_hvac(house, "heat_cool")
    fail["n"] = 1
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)  # restore fails -> marker pending
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    for m in range(1, 4):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert house.ha.mode == "off"
    for m in range(0, 4):
        house.at(*MON, 17, m)
        await house.tick_all()
    assert house.ha.mode != "off", "gap handback no longer restores ClimateIQ's own off"
