"""Keep Active (AI) mode honest to the user's schedules.

In Active mode the LLM picks the thermostat setpoint, but a schedule is the
household's stated intent ("the nursery is 68°F at 7pm because that's
bedtime").  The LLM may decide *how* to get there — push harder, use fans,
precondition early — but it must never trade the schedule target away for
comfort bands, occupancy guesses, or energy savings.

The reference point is the setpoint Schedule mode itself would send (the
schedule target run through offset compensation).  Relative to that:

- Focus zones too warm  → the setpoint may not be *warmer* than Schedule mode's.
- Focus zones too cold  → the setpoint may not be *cooler* than Schedule mode's.
- Focus zones on target → the setpoint stays within a small slack of it.

So the AI can only ever be equal to, or more aggressive toward the target
than, plain Schedule mode.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

# Focus-zone error (°C) inside which the zones count as "on target".  Matches
# the Active-mode steady-state gate.
SCHEDULE_DEADBAND_C: float = 0.5

# How far (°C) the AI may wander from Schedule mode's setpoint while the
# focus zones are already on target — enough to help an out-of-band
# constraint zone, not enough to walk the focus zones off target.
SCHEDULE_HOLD_SLACK_C: float = 0.5


def enforce_schedule_setpoint(
    recommended_c: float,
    schedule_setpoint_c: float,
    focus_avg_c: float | None,
    target_c: float,
    *,
    deadband_c: float = SCHEDULE_DEADBAND_C,
    hold_slack_c: float = SCHEDULE_HOLD_SLACK_C,
) -> tuple[float, str | None]:
    """Clamp an AI-recommended setpoint so it can't work against the schedule.

    Args:
        recommended_c: The setpoint the AI wants (Celsius).
        schedule_setpoint_c: The setpoint Schedule mode would send for the
            same schedule (target + offset compensation), Celsius.
        focus_avg_c: Current average temperature of the schedule's zones, or
            None when no reading is available.
        target_c: The schedule's desired zone temperature (Celsius).

    Returns:
        (setpoint_c, note) — ``note`` describes the clamp when one was
        applied, otherwise None.
    """
    if focus_avg_c is None:
        # No zone reading: we can't tell which way to push, so stay close to
        # what Schedule mode would do.
        error_c = 0.0
    else:
        error_c = focus_avg_c - target_c

    if error_c > deadband_c:
        # Too warm — must be at least as cool as Schedule mode.
        if recommended_c > schedule_setpoint_c:
            return schedule_setpoint_c, (
                f"zones {error_c:+.1f}°C over schedule target; capped at "
                f"schedule setpoint {schedule_setpoint_c:.1f}°C"
            )
        return recommended_c, None

    if error_c < -deadband_c:
        # Too cold — must be at least as warm as Schedule mode.
        if recommended_c < schedule_setpoint_c:
            return schedule_setpoint_c, (
                f"zones {error_c:+.1f}°C under schedule target; raised to "
                f"schedule setpoint {schedule_setpoint_c:.1f}°C"
            )
        return recommended_c, None

    lo = schedule_setpoint_c - hold_slack_c
    hi = schedule_setpoint_c + hold_slack_c
    if recommended_c < lo or recommended_c > hi:
        clamped = max(lo, min(hi, recommended_c))
        return clamped, (
            f"zones on schedule target; held within {hold_slack_c:.1f}°C of "
            f"schedule setpoint {schedule_setpoint_c:.1f}°C"
        )
    return recommended_c, None


def moves_toward_target(
    current_setpoint_c: float,
    new_setpoint_c: float,
    focus_avg_c: float | None,
    target_c: float,
    *,
    deadband_c: float = SCHEDULE_DEADBAND_C,
) -> bool:
    """True when a setpoint change drives off-target focus zones toward target.

    Used to let schedule-driven corrections through the setpoint
    anti-oscillation lockout.
    """
    if focus_avg_c is None:
        return False
    error_c = focus_avg_c - target_c
    if error_c > deadband_c:
        return new_setpoint_c < current_setpoint_c
    if error_c < -deadband_c:
        return new_setpoint_c > current_setpoint_c
    return False


def precondition_allowed(
    zone_temp_c: float | None,
    upcoming_target_c: float,
    thermostat_mode: str,
    *,
    deadband_c: float = SCHEDULE_DEADBAND_C,
) -> bool:
    """Whether starting an upcoming schedule early is real preconditioning.

    Preconditioning means running the HVAC *in its current direction* so a
    room reaches the next schedule's target by its start time — warming up
    before the morning schedule in heat mode, cooling down before bedtime in
    cool mode.  It is NOT preconditioning to raise the setpoint while
    cooling (or lower it while heating): that just switches the HVAC off
    early and abandons the schedule that is active right now.  Example:
    Sleeping 69°F in cool mode, Daytime 72°F at 6:30 — the house should hold
    69°F until 6:30, not drift to 72°F from 3:30.

    Gaps inside the dead-band don't need a head start either.
    """
    if zone_temp_c is None:
        return False
    mode = (thermostat_mode or "").lower()
    gap_c = upcoming_target_c - zone_temp_c
    if gap_c > deadband_c:
        return "heat" in mode
    if gap_c < -deadband_c:
        return "cool" in mode
    return False


def schedule_active_at(
    days_of_week: list[int] | None,
    start_time: str,
    end_time: str | None,
    now_local: datetime,
) -> bool:
    """Whether a schedule's window contains ``now_local`` (local wall time).

    Same rules as the scheduler: ``days_of_week`` 0=Mon, no end = until
    23:59, and an end before the start wraps past midnight.
    """
    if now_local.weekday() not in (days_of_week or []):
        return False
    try:
        s_h, s_m = map(int, str(start_time).split(":")[:2])
        start_t = time(s_h, s_m)
    except (ValueError, TypeError):
        return False
    end_t = time(23, 59)
    if end_time:
        try:
            e_h, e_m = map(int, str(end_time).split(":")[:2])
            end_t = time(e_h, e_m)
        except (ValueError, TypeError):
            pass
    cur = now_local.time().replace(second=0, microsecond=0)
    if end_t < start_t:
        return cur >= start_t or cur <= end_t
    return start_t <= cur <= end_t


def other_schedule_active(
    schedules: list[Any],
    upcoming_id: Any,
    now_local: datetime,
) -> Any | None:
    """Return a schedule (other than ``upcoming_id``) that is active right now.

    Preconditioning must never override a schedule the user set for the
    current time: with one thermostat, warming the house for the 6:30
    "Daytime" schedule also heats the bedrooms that the still-active
    "Sleeping" schedule wants at 69°F.  Early starts only happen in gaps
    between schedules.
    """
    for sched in schedules:
        if getattr(sched, "id", None) == upcoming_id:
            continue
        if schedule_active_at(
            getattr(sched, "days_of_week", None),
            getattr(sched, "start_time", ""),
            getattr(sched, "end_time", None),
            now_local,
        ):
            return sched
    return None
