"""SafetyProtocol.vet — directional-effectiveness guard.

Regression cover for the failure seen in production: the advisor returned
68°F while the schedule wanted 69°F, the zones sat at 66°F and the
thermostat's own sensor read 69°F.  The thermostat was already satisfied,
so it stayed idle and the rooms never warmed up.
"""

import pytest

from backend.core.climate_advisor import AdvisorDecision, SafetyProtocol


def _f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _adjust(setpoint_f: float) -> AdvisorDecision:
    return AdvisorDecision(
        action="adjust",
        setpoint_c=_c(setpoint_f),
        wait_until=None,
        reasoning="test",
        from_llm=True,
    )


def test_heat_setpoint_below_thermostat_reading_is_raised():
    """Zones cold + setpoint the thermostat already satisfies → forced above it."""
    vetted = SafetyProtocol.vet(
        _adjust(68), desired_temp_c=_c(69), max_offset_f=8.0, hvac_mode="heat",
        zone_avg_c=_c(66), thermostat_c=_c(69),
    )
    assert round(_f(vetted.setpoint_c)) == 70
    assert _f(vetted.setpoint_c) > _f(_c(69))  # strictly above thermostat reading


def test_heat_floor_is_schedule_target_when_thermostat_is_cold():
    """Thermostat below target → floor is the schedule target, not thermostat+1."""
    vetted = SafetyProtocol.vet(
        _adjust(66), desired_temp_c=_c(69), max_offset_f=8.0, hvac_mode="heat",
        zone_avg_c=_c(66), thermostat_c=_c(64),
    )
    assert round(_f(vetted.setpoint_c)) == 69


def test_heat_setpoint_above_floor_is_untouched():
    """A directionally-sound advisor setpoint passes through unchanged."""
    vetted = SafetyProtocol.vet(
        _adjust(72), desired_temp_c=_c(69), max_offset_f=8.0, hvac_mode="heat",
        zone_avg_c=_c(66), thermostat_c=_c(69),
    )
    assert round(_f(vetted.setpoint_c)) == 72


def test_cool_setpoint_above_thermostat_reading_is_lowered():
    """Mirror case: zones hot, setpoint the AC has already satisfied."""
    vetted = SafetyProtocol.vet(
        _adjust(76), desired_temp_c=_c(74), max_offset_f=8.0, hvac_mode="cool",
        zone_avg_c=_c(78), thermostat_c=_c(74),
    )
    assert round(_f(vetted.setpoint_c)) == 73


def test_direction_rule_respects_max_offset_cap():
    """Rule 3 must not push the setpoint past the max-offset ceiling."""
    vetted = SafetyProtocol.vet(
        _adjust(60), desired_temp_c=_c(69), max_offset_f=2.0, hvac_mode="heat",
        zone_avg_c=_c(60), thermostat_c=_c(80),
    )
    assert _f(vetted.setpoint_c) == pytest.approx(71.0, abs=0.1)


def test_zones_within_deadband_are_not_forced():
    """Sub-1°F deviations self-correct — no forced setpoint."""
    vetted = SafetyProtocol.vet(
        _adjust(68), desired_temp_c=_c(69), max_offset_f=8.0, hvac_mode="heat",
        zone_avg_c=_c(68.5), thermostat_c=_c(69),
    )
    assert round(_f(vetted.setpoint_c)) == 68


def test_hold_and_wait_decisions_bypass_vetting():
    hold = AdvisorDecision(
        action="hold", setpoint_c=_c(68), wait_until=None,
        reasoning="test", from_llm=True,
    )
    assert SafetyProtocol.vet(
        hold, desired_temp_c=_c(69), max_offset_f=8.0, hvac_mode="heat",
        zone_avg_c=_c(66), thermostat_c=_c(69),
    ) is hold


def test_missing_readings_skip_the_direction_rule():
    """No zone data → Rule 3 cannot be evaluated; earlier rules still apply."""
    vetted = SafetyProtocol.vet(
        _adjust(68), desired_temp_c=_c(69), max_offset_f=8.0, hvac_mode="heat",
        zone_avg_c=None, thermostat_c=None,
    )
    assert round(_f(vetted.setpoint_c)) == 68
