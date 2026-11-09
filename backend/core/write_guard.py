"""Final guard applied to every automatic thermostat setpoint (spec §5).

Every automatic loop (schedule maintenance, Active/AI, Follow-Me) computes a
*desired* setpoint its own way — offset compensation, the LLM advisor, a
cached "hold"/"wait", a time-window park.  Previously each path enforced the
§5 rules separately, and later steps undid earlier ones (the advisor's
max-offset cap pulled a parked setpoint back above the thermostat reading; a
stale "hold" re-wrote a value that heated rooms already past target; the
0.5 °C "already at target" tolerance let a 1 °F-too-high setpoint stand).

``guard_setpoint`` is the single, last step.  It returns the *allowed band*
for the setpoint plus the preferred value inside it.  Callers write only when
the thermostat's current setpoint is outside the band, which also stops
minute-to-minute churn when the thermostat's reading flickers.

All values are °C.  The thermostat acts on whole °F, so margins are 1 °F and
values are quantised to whole °F in the safe direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ONE_F_C = 5.0 / 9.0
# Zones within this of target (°C) count as "on target" (≈1 °F) — same
# dead-band as direction selection.
DEADBAND_C = 0.6
# Plausible thermostat readings (°C).  Outside this the reading is ignored
# (a dying override sensor must not drive the setpoint to 36 °F).
_READING_MIN_C = -10.0
_READING_MAX_C = 45.0


@dataclass(frozen=True)
class GuardResult:
    """Allowed setpoint band and the value to write if outside it."""

    write: bool                 # False → don't touch the setpoint at all
    setpoint_c: float           # preferred value (whole °F, in °C)
    lo_c: float                 # current setpoint in [lo, hi] → no write needed
    hi_c: float
    reason: str


def _f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _floor_f(c: float) -> float:
    """Round down to a whole °F (safe for 'heat must stay off')."""
    return _c(math.floor(_f(c) + 1e-6))


def _ceil_f(c: float) -> float:
    """Round up to a whole °F (safe for 'cool must stay off')."""
    return _c(math.ceil(_f(c) - 1e-6))


def _round_f(c: float) -> float:
    return _c(round(_f(c)))


def guard_setpoint(
    *,
    desired_c: float,
    target_c: float,
    zone_avg_c: float | None,
    thermostat_c: float | None,
    hvac_mode: str,
    direction_allowed: bool = True,
    max_offset_c: float = 8 * ONE_F_C,
    safety_min_c: float,
    safety_max_c: float,
) -> GuardResult:
    """Apply spec §5.3-§5.6 to a desired setpoint for a heat or cool thermostat.

    Args:
        desired_c: what the caller wants (formula / advisor / AI / held value).
        target_c: the governing target for the zones.
        zone_avg_c: governing zones' average; None → no write (§9.2).
        thermostat_c: the thermostat's own reading (None or implausible →
            the reading-relative rules are skipped).
        hvac_mode: the thermostat's actual current mode.
        direction_allowed: False when the current mode's direction is
            forbidden (time window, lock) → park so it can't run (§5.5).
    """
    mode = (hvac_mode or "").lower()
    if zone_avg_c is None:
        return GuardResult(False, desired_c, -math.inf, math.inf, "no zone readings")
    if mode not in ("heat", "cool"):
        # off / heat_cool / unknown: never write a single setpoint here.
        return GuardResult(False, desired_c, -math.inf, math.inf, f"mode {mode or 'unknown'}")

    reading = thermostat_c
    if reading is not None and not (_READING_MIN_C <= reading <= _READING_MAX_C):
        reading = None

    error_c = target_c - zone_avg_c  # + → rooms below target
    heat = mode == "heat"

    # Band: start from the max-offset limits around target.
    lo = target_c - max_offset_c
    hi = target_c + max_offset_c
    value = desired_c
    reason = "within limits"

    if heat:
        must_stay_off = (not direction_allowed) or error_c <= 0
        must_run = direction_allowed and error_c > DEADBAND_C
        if must_stay_off:
            # §5.3/§5.5: at or below min(target, reading - 1 °F) → heat off.
            cap = target_c if reading is None else min(target_c, reading - ONE_F_C)
            if not direction_allowed and reading is not None:
                cap = reading - ONE_F_C
            cap = _floor_f(cap)
            lo, hi = -math.inf, cap
            value = min(_floor_f(desired_c), cap)
            reason = "heat blocked" if not direction_allowed else "rooms at/above target"
        elif must_run:
            # §5.4: strictly above the reading (and ≥ target) so heat runs.
            floor = target_c if reading is None else max(target_c, reading + ONE_F_C)
            floor = _ceil_f(floor)
            lo = floor
            hi = max(hi, floor)
            value = max(_round_f(desired_c), floor)
            reason = "rooms below target — heat must run"
        else:
            # Slightly below target (inside the dead-band): don't overshoot.
            hi = min(hi, _ceil_f(target_c if reading is None else max(target_c, reading)))
            value = min(_round_f(desired_c), hi)
    else:
        must_stay_off = (not direction_allowed) or error_c >= 0
        must_run = direction_allowed and error_c < -DEADBAND_C
        if must_stay_off:
            floor = target_c if reading is None else max(target_c, reading + ONE_F_C)
            if not direction_allowed and reading is not None:
                floor = reading + ONE_F_C
            floor = _ceil_f(floor)
            lo, hi = floor, math.inf
            value = max(_ceil_f(desired_c), floor)
            reason = "cool blocked" if not direction_allowed else "rooms at/below target"
        elif must_run:
            cap = target_c if reading is None else min(target_c, reading - ONE_F_C)
            cap = _floor_f(cap)
            hi = cap
            lo = min(lo, cap)
            value = min(_round_f(desired_c), cap)
            reason = "rooms above target — cool must run"
        else:
            lo = max(lo, _floor_f(target_c if reading is None else min(target_c, reading)))
            value = max(_round_f(desired_c), lo)

    value = min(max(value, lo), hi)

    # §5.6 safety range — last, always.
    s_lo, s_hi = _ceil_f(safety_min_c), _floor_f(safety_max_c)
    value = min(max(value, s_lo), s_hi)
    lo = min(max(lo, s_lo), s_hi)
    hi = min(max(hi, s_lo), s_hi)
    if lo > hi:
        lo = hi = value
    return GuardResult(True, value, lo, hi, reason)


def needs_write(result: GuardResult, current_setpoint_c: float | None) -> bool:
    """Write only when the current setpoint is outside the allowed band.

    The band is in °C; compare with a small tolerance for °F→°C round trips.
    """
    if not result.write:
        return False
    if current_setpoint_c is None:
        return True
    tol = 0.05
    return not (result.lo_c - tol <= current_setpoint_c <= result.hi_c + tol)
