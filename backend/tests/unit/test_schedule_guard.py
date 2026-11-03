"""Unit tests for Active-mode schedule enforcement."""

from __future__ import annotations

import pytest

from backend.core.schedule_guard import enforce_schedule_setpoint, moves_toward_target

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
