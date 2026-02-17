"""Unit tests for schedule helper functions and Pydantic validation."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.api.routes.schedule import (
    ScheduleCreate,
    check_schedule_overlap,
    get_next_occurrence,
    parse_time,
)

# ============================================================================
# parse_time tests
# ============================================================================


class TestParseTime:
    def test_morning_time(self) -> None:
        assert parse_time("08:30") == time(8, 30)

    def test_midnight(self) -> None:
        assert parse_time("00:00") == time(0, 0)

    def test_end_of_day(self) -> None:
        assert parse_time("23:59") == time(23, 59)

    def test_noon(self) -> None:
        assert parse_time("12:00") == time(12, 0)

    def test_single_digit_hour_padded(self) -> None:
        assert parse_time("01:05") == time(1, 5)


# ============================================================================
# get_next_occurrence tests
# ============================================================================


class TestGetNextOccurrence:
    """All tests use a fixed reference time: Monday 2026-02-16 10:00 UTC."""

    FIXED = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)  # Monday

    def test_today_time_in_future(self) -> None:
        """Same day, later time -> returns today at that time."""
        result = get_next_occurrence(
            days_of_week=[0],  # Monday
            start_time="14:00",
            from_time=self.FIXED,
        )
        expected = self.FIXED.replace(hour=14, minute=0, second=0, microsecond=0)
        assert result == expected

    def test_today_time_already_passed(self) -> None:
        """Same day, earlier time -> returns next week."""
        result = get_next_occurrence(
            days_of_week=[0],  # Monday only
            start_time="08:00",
            from_time=self.FIXED,
        )
        # Next Monday is 7 days ahead
        expected = (self.FIXED + timedelta(days=7)).replace(
            hour=8, minute=0, second=0, microsecond=0
        )
        assert result == expected

    def test_tomorrow(self) -> None:
        """Tomorrow's day -> returns tomorrow at that time."""
        result = get_next_occurrence(
            days_of_week=[1],  # Tuesday
            start_time="09:00",
            from_time=self.FIXED,
        )
        expected = (self.FIXED + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        assert result == expected

    def test_multiple_days_returns_nearest(self) -> None:
        """Multiple days -> returns the nearest future occurrence."""
        result = get_next_occurrence(
            days_of_week=[2, 4],  # Wednesday, Friday
            start_time="07:00",
            from_time=self.FIXED,
        )
        # Wednesday is 2 days ahead from Monday
        expected = (self.FIXED + timedelta(days=2)).replace(
            hour=7, minute=0, second=0, microsecond=0
        )
        assert result == expected

    def test_today_exact_current_time_not_in_future(self) -> None:
        """If current_time == target_time, it's not strictly < so skip to next week."""
        fixed_exact = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
        result = get_next_occurrence(
            days_of_week=[0],  # Monday only
            start_time="10:00",
            from_time=fixed_exact,
        )
        # current_time is NOT < target_time, so next Monday
        expected = (fixed_exact + timedelta(days=7)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        assert result == expected

    def test_weekend_from_monday(self) -> None:
        """Saturday schedule from Monday -> 5 days ahead."""
        result = get_next_occurrence(
            days_of_week=[5],  # Saturday
            start_time="12:00",
            from_time=self.FIXED,
        )
        expected = (self.FIXED + timedelta(days=5)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        assert result == expected

    def test_every_day_time_in_future(self) -> None:
        """All days selected, time in future -> returns today."""
        result = get_next_occurrence(
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time="18:00",
            from_time=self.FIXED,
        )
        expected = self.FIXED.replace(hour=18, minute=0, second=0, microsecond=0)
        assert result == expected

    def test_every_day_time_passed(self) -> None:
        """All days selected, time passed -> returns tomorrow."""
        result = get_next_occurrence(
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time="06:00",
            from_time=self.FIXED,
        )
        expected = (self.FIXED + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        assert result == expected


# ============================================================================
# check_schedule_overlap tests
# ============================================================================


class TestCheckScheduleOverlap:
    def test_same_day_overlapping_times(self) -> None:
        s1 = {"days_of_week": [0], "start_time": "08:00", "end_time": "12:00", "zone_id": None}
        s2 = {"days_of_week": [0], "start_time": "10:00", "end_time": "14:00", "zone_id": None}
        assert check_schedule_overlap(s1, s2) is True

    def test_same_day_non_overlapping_times(self) -> None:
        s1 = {"days_of_week": [0], "start_time": "06:00", "end_time": "09:00", "zone_id": None}
        s2 = {"days_of_week": [0], "start_time": "10:00", "end_time": "14:00", "zone_id": None}
        assert check_schedule_overlap(s1, s2) is False

    def test_different_days(self) -> None:
        s1 = {"days_of_week": [0], "start_time": "08:00", "end_time": "12:00", "zone_id": None}
        s2 = {"days_of_week": [2], "start_time": "08:00", "end_time": "12:00", "zone_id": None}
        assert check_schedule_overlap(s1, s2) is False

    def test_different_zones(self) -> None:
        zone_a = str(uuid4())
        zone_b = str(uuid4())
        s1 = {
            "days_of_week": [0],
            "start_time": "08:00",
            "end_time": "12:00",
            "zone_id": zone_a,
        }
        s2 = {
            "days_of_week": [0],
            "start_time": "08:00",
            "end_time": "12:00",
            "zone_id": zone_b,
        }
        assert check_schedule_overlap(s1, s2) is False

    def test_one_zone_none_always_overlaps(self) -> None:
        """zone_id=None means 'all zones', so it always overlaps with any zone."""
        zone_a = str(uuid4())
        s1 = {"days_of_week": [0], "start_time": "08:00", "end_time": "12:00", "zone_id": None}
        s2 = {
            "days_of_week": [0],
            "start_time": "08:00",
            "end_time": "12:00",
            "zone_id": zone_a,
        }
        assert check_schedule_overlap(s1, s2) is True

    def test_boundary_one_ends_when_other_starts(self) -> None:
        """start1 < end2 and start2 < end1 — boundary case: no overlap."""
        s1 = {"days_of_week": [0], "start_time": "06:00", "end_time": "09:00", "zone_id": None}
        s2 = {"days_of_week": [0], "start_time": "09:00", "end_time": "12:00", "zone_id": None}
        # 06:00 < 12:00 is True, but 09:00 < 09:00 is False → no overlap
        assert check_schedule_overlap(s1, s2) is False

    def test_contained_schedule(self) -> None:
        """One schedule fully contained within another."""
        s1 = {"days_of_week": [0], "start_time": "06:00", "end_time": "18:00", "zone_id": None}
        s2 = {"days_of_week": [0], "start_time": "10:00", "end_time": "14:00", "zone_id": None}
        assert check_schedule_overlap(s1, s2) is True

    def test_no_end_time_defaults_to_2359(self) -> None:
        """When end_time is None, it defaults to 23:59."""
        s1 = {"days_of_week": [0], "start_time": "20:00", "end_time": None, "zone_id": None}
        s2 = {"days_of_week": [0], "start_time": "22:00", "end_time": None, "zone_id": None}
        assert check_schedule_overlap(s1, s2) is True

    def test_multiple_shared_days(self) -> None:
        """Schedules share some but not all days, with overlapping times."""
        s1 = {
            "days_of_week": [0, 1, 2],
            "start_time": "08:00",
            "end_time": "12:00",
            "zone_id": None,
        }
        s2 = {
            "days_of_week": [2, 3, 4],
            "start_time": "10:00",
            "end_time": "14:00",
            "zone_id": None,
        }
        assert check_schedule_overlap(s1, s2) is True


# ============================================================================
# ScheduleCreate validation tests
# ============================================================================


class TestScheduleCreateValidation:
    def _make_payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "name": "Test Schedule",
            "start_time": "08:00",
            "target_temp_c": 22.0,
            "days_of_week": [0, 1, 2, 3, 4],
        }
        base.update(overrides)
        return base

    def test_temperature_below_safety_minimum(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreate(**self._make_payload(target_temp_c=4.3))
        errors = exc_info.value.errors()
        assert any("4.4" in str(e) or "greater than" in str(e) for e in errors)

    def test_temperature_above_safety_maximum(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreate(**self._make_payload(target_temp_c=37.9))
        errors = exc_info.value.errors()
        assert any("37.8" in str(e) or "less than" in str(e) for e in errors)

    def test_temperature_at_lower_bound(self) -> None:
        # ScheduleBase enforces ge=10.0 before the safety validator runs
        schedule = ScheduleCreate(**self._make_payload(target_temp_c=10.0))
        assert schedule.target_temp_c == 10.0

    def test_temperature_at_upper_bound(self) -> None:
        # ScheduleBase enforces le=35.0 before the safety validator runs
        schedule = ScheduleCreate(**self._make_payload(target_temp_c=35.0))
        assert schedule.target_temp_c == 35.0

    def test_valid_temperature(self) -> None:
        schedule = ScheduleCreate(**self._make_payload(target_temp_c=22.0))
        assert schedule.target_temp_c == 22.0

    def test_empty_days_of_week(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreate(**self._make_payload(days_of_week=[]))
        errors = exc_info.value.errors()
        assert any("day" in str(e).lower() for e in errors)

    def test_invalid_day_too_high(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreate(**self._make_payload(days_of_week=[7]))
        errors = exc_info.value.errors()
        assert any("day" in str(e).lower() or "Days" in str(e) for e in errors)

    def test_invalid_day_negative(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreate(**self._make_payload(days_of_week=[-1]))
        errors = exc_info.value.errors()
        assert any("day" in str(e).lower() or "Days" in str(e) for e in errors)

    def test_days_are_sorted_and_deduplicated(self) -> None:
        schedule = ScheduleCreate(**self._make_payload(days_of_week=[4, 2, 2, 0]))
        assert schedule.days_of_week == [0, 2, 4]

    def test_name_required(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleCreate(  # type: ignore[call-arg]
                start_time="08:00",
                target_temp_c=22.0,
                days_of_week=[0],
            )

    def test_start_time_format_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleCreate(**self._make_payload(start_time="8:00"))

    def test_priority_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleCreate(**self._make_payload(priority=11))

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleCreate(**self._make_payload(unknown_field="bad"))
