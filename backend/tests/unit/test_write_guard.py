"""Unit tests for the final setpoint guard (docs/CONTROL_SPEC.md §5)."""

from __future__ import annotations

from typing import Any

from backend.core.write_guard import ONE_F_C, guard_setpoint, needs_write


def c(f: float) -> float:
    return (f - 32) * 5 / 9


def f(c_val: float) -> float:
    return c_val * 9 / 5 + 32


SAFE: dict[str, Any] = {"safety_min_c": 4.4, "safety_max_c": 37.8}


def test_heat_park_is_below_reading_with_margin() -> None:
    # zones 72.5 > target 72, reading 70.4 → setpoint ≤ 69 (whole °F, 1 °F margin)
    r = guard_setpoint(desired_c=c(74), target_c=c(72), zone_avg_c=c(72.5),
                       thermostat_c=c(70.4), hvac_mode="heat", **SAFE)
    assert round(f(r.setpoint_c)) <= 69
    assert needs_write(r, c(71))          # 71 would still fire the furnace
    assert not needs_write(r, c(68))      # already parked low enough


def test_max_offset_cannot_undo_the_park() -> None:
    # max offset 4°F, zones 73 > 72 target, hallway 67 → must stay ≤ 66
    r = guard_setpoint(desired_c=c(68), target_c=c(72), zone_avg_c=c(73),
                       thermostat_c=c(67), hvac_mode="heat", max_offset_c=4 * ONE_F_C, **SAFE)
    assert f(r.setpoint_c) <= 66.01


def test_heat_must_run_strictly_above_reading() -> None:
    r = guard_setpoint(desired_c=c(72), target_c=c(72), zone_avg_c=c(70),
                       thermostat_c=c(72.3), hvac_mode="heat", **SAFE)
    assert f(r.setpoint_c) >= 74 - 0.01   # ceil(72.3 + 1) = 74


def test_cool_mirror() -> None:
    park = guard_setpoint(desired_c=c(70), target_c=c(72), zone_avg_c=c(71.5),
                          thermostat_c=c(74), hvac_mode="cool", **SAFE)
    assert f(park.setpoint_c) >= 75 - 0.01
    run = guard_setpoint(desired_c=c(72), target_c=c(72), zone_avg_c=c(74),
                         thermostat_c=c(71.7), hvac_mode="cool", **SAFE)
    assert f(run.setpoint_c) <= 70 + 0.01


def test_blocked_direction_parks_below_reading() -> None:
    r = guard_setpoint(desired_c=c(72), target_c=c(70), zone_avg_c=c(66),
                       thermostat_c=c(68), hvac_mode="heat", direction_allowed=False, **SAFE)
    assert f(r.setpoint_c) <= 67.01


def test_implausible_reading_ignored_and_safety_clamp_last() -> None:
    r = guard_setpoint(desired_c=c(72), target_c=c(70), zone_avg_c=c(66),
                       thermostat_c=3.0 - 20, hvac_mode="heat", direction_allowed=False, **SAFE)
    assert r.setpoint_c >= 4.4 - 0.01


def test_no_zone_data_means_no_write() -> None:
    r = guard_setpoint(desired_c=c(72), target_c=c(70), zone_avg_c=None,
                       thermostat_c=c(68), hvac_mode="heat", **SAFE)
    assert not r.write and not needs_write(r, c(60))


def test_heat_cool_and_off_are_not_written_here() -> None:
    for mode in ("heat_cool", "off", "auto"):
        r = guard_setpoint(desired_c=c(72), target_c=c(70), zone_avg_c=c(66),
                           thermostat_c=c(68), hvac_mode=mode, **SAFE)
        assert not r.write


def test_aggressive_ai_value_inside_band_is_kept() -> None:
    # Rooms cold: an AI setpoint above the floor is acceptable — no churn.
    r = guard_setpoint(desired_c=c(72), target_c=c(70), zone_avg_c=c(66),
                       thermostat_c=c(68), hvac_mode="heat", **SAFE)
    assert not needs_write(r, c(76))
