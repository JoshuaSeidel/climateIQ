"""Unit tests for Active-mode schedule enforcement."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.core.schedule_guard import (
    enforce_schedule_setpoint,
    moves_toward_target,
    other_schedule_active,
    precondition_allowed,
    schedule_active_at,
)

# Bedtime: nursery target 20°C (68°F).


class TestEnforceScheduleSetpoint:
    def test_too_warm_caps_timid_ai_at_schedule_setpoint(self) -> None:
        # Nursery 22°C, bedtime wants 20°C; schedule mode would send 19°C.
        # AI wants to hold 22°C because the room is "vacant / in band".
        sp, note = enforce_schedule_setpoint(22.0, 19.0, 22.0, 20.0)
        assert sp == 19.0
        assert note is not None

    def test_too_warm_allows_more_aggressive_ai(self) -> None:
        sp, note = enforce_schedule_setpoint(18.5, 19.0, 22.0, 20.0)
        assert sp == 18.5
        assert note is None

    def test_too_cold_raises_timid_ai_to_schedule_setpoint(self) -> None:
        sp, note = enforce_schedule_setpoint(18.0, 22.0, 18.5, 20.0)
        assert sp == 22.0
        assert note is not None

    def test_too_cold_allows_more_aggressive_ai(self) -> None:
        sp, note = enforce_schedule_setpoint(23.0, 22.0, 18.5, 20.0)
        assert sp == 23.0
        assert note is None

    def test_on_target_holds_within_slack(self) -> None:
        sp, note = enforce_schedule_setpoint(23.0, 20.0, 20.2, 20.0)
        assert sp == pytest.approx(20.5)
        assert note is not None
        sp, note = enforce_schedule_setpoint(17.0, 20.0, 20.2, 20.0)
        assert sp == pytest.approx(19.5)

    def test_on_target_small_adjustment_passes(self) -> None:
        sp, note = enforce_schedule_setpoint(20.3, 20.0, 20.1, 20.0)
        assert sp == 20.3
        assert note is None

    def test_no_reading_stays_near_schedule_setpoint(self) -> None:
        sp, note = enforce_schedule_setpoint(25.0, 20.0, None, 20.0)
        assert sp == pytest.approx(20.5)
        assert note is not None


class TestMovesTowardTarget:
    def test_too_warm_lowering_counts(self) -> None:
        assert moves_toward_target(22.0, 19.0, 22.0, 20.0)

    def test_too_warm_raising_does_not(self) -> None:
        assert not moves_toward_target(19.0, 22.0, 22.0, 20.0)

    def test_too_cold_raising_counts(self) -> None:
        assert moves_toward_target(18.0, 22.0, 18.5, 20.0)

    def test_on_target_never_bypasses(self) -> None:
        assert not moves_toward_target(20.0, 21.0, 20.2, 20.0)

    def test_no_reading_never_bypasses(self) -> None:
        assert not moves_toward_target(20.0, 18.0, None, 20.0)


class TestPreconditionAllowed:
    def test_night_cool_to_warmer_daytime_is_not_preconditioning(self) -> None:
        # Sleeping 69°F (20.6°C) in cool mode, Daytime 72°F (22.2°C) next:
        # raising the setpoint early just stops the AC — not allowed.
        assert not precondition_allowed(21.9, 22.2, "cool")
        assert not precondition_allowed(20.6, 22.2, "cool")

    def test_heat_warm_up_before_morning(self) -> None:
        assert precondition_allowed(19.0, 21.0, "heat")

    def test_cool_down_before_bedtime(self) -> None:
        assert precondition_allowed(23.5, 21.0, "cool")

    def test_small_gap_skips(self) -> None:
        assert not precondition_allowed(21.8, 22.2, "heat")

    def test_no_reading_skips(self) -> None:
        assert not precondition_allowed(None, 22.2, "heat")


# Regression: 2026-11-09 05:15 — Daytime's warm-up overrode Sleeping.
_ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


def _sched(sid: str, start: str, end: str) -> SimpleNamespace:
    return SimpleNamespace(id=sid, days_of_week=list(_ALL_DAYS), start_time=start, end_time=end)


class TestOtherScheduleActive:
    def test_sleeping_blocks_daytime_warmup_before_dawn(self) -> None:
        sleeping = _sched("sleep", "20:00", "06:30")
        daytime = _sched("day", "06:30", "18:00")
        found = other_schedule_active([sleeping, daytime], "day", datetime(2026, 11, 9, 5, 15))
        assert found is sleeping

    def test_gap_allows_warmup(self) -> None:
        daytime = _sched("day", "06:30", "18:00")
        evening = _sched("oliver", "18:00", "20:00")
        assert other_schedule_active([daytime, evening], "day", datetime(2026, 11, 9, 5, 15)) is None

    def test_schedule_active_at_wraps_midnight(self) -> None:
        days = list(_ALL_DAYS)
        assert schedule_active_at(days, "20:00", "06:30", datetime(2026, 11, 9, 23, 0))
        assert schedule_active_at(days, "20:00", "06:30", datetime(2026, 11, 9, 6, 30))
        assert not schedule_active_at(days, "20:00", "06:30", datetime(2026, 11, 9, 12, 0))
        assert not schedule_active_at([5, 6], "20:00", "06:30", datetime(2026, 11, 9, 23, 0))
