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
    # Exact modes only: heat_cool contains both words and must not count.
    if gap_c > deadband_c:
        return mode == "heat"
    if gap_c < -deadband_c:
        return mode == "cool"
    return False


def _parse_hhmm(value: Any) -> time | None:
    try:
        h, m = map(int, str(value).split(":")[:2])
        return time(h, m)
    except (ValueError, TypeError):
        return None


def active_occurrence(
    days_of_week: list[int] | None,
    start_time: str,
    end_time: str | None,
    now_local: datetime,
) -> tuple[datetime, datetime] | None:
    """The (start, end) of the occurrence containing ``now_local``, or None.

    Spec §3.1: the window is ``[start, end)``.  ``days_of_week`` (0=Mon)
    names the day an occurrence *starts*; an end at or before the start
    wraps past midnight and the after-midnight part belongs to the start day
    (a Friday 20:00-06:30 schedule is active Saturday 03:00 even if Saturday
    isn't ticked).  end == start means a full 24 h; no end time means until
    midnight.  End-exclusive, so back-to-back schedules (06:30 end / 06:30
    start) never overlap.
    """
    from datetime import timedelta

    start_t = _parse_hhmm(start_time)
    if start_t is None:
        return None
    end_t = _parse_hhmm(end_time) if end_time else None
    days = set(days_of_week or [])
    now = now_local.replace(second=0, microsecond=0)
    for day_offset in (0, -1):
        start_day = (now + timedelta(days=day_offset)).date()
        if start_day.weekday() not in days:
            continue
        start_dt = now.replace(
            year=start_day.year, month=start_day.month, day=start_day.day,
            hour=start_t.hour, minute=start_t.minute,
        )
        if end_t is None:
            end_dt = start_dt.replace(hour=0, minute=0) + timedelta(days=1)
        else:
            end_dt = start_dt.replace(hour=end_t.hour, minute=end_t.minute)
            if end_t <= start_t:
                end_dt += timedelta(days=1)
        if start_dt <= now < end_dt:
            return start_dt, end_dt
    return None


def schedule_active_at(
    days_of_week: list[int] | None,
    start_time: str,
    end_time: str | None,
    now_local: datetime,
) -> bool:
    """Whether a schedule's window contains ``now_local`` (spec §3.1)."""
    return active_occurrence(days_of_week, start_time, end_time, now_local) is not None


def find_active_schedule(schedules: list[Any], now_local: datetime) -> Any | None:
    """The schedule in effect at ``now_local`` (spec §3).

    Enabled schedules whose window contains now; highest ``priority`` wins,
    ties go to the most recently started occurrence.  This is the single
    implementation used by every control loop and the API.
    """
    best: Any | None = None
    best_key: tuple[int, datetime] | None = None
    for sched in schedules:
        if getattr(sched, "is_enabled", True) is False:
            continue
        occ = active_occurrence(
            getattr(sched, "days_of_week", None),
            getattr(sched, "start_time", ""),
            getattr(sched, "end_time", None),
            now_local,
        )
        if occ is None:
            continue
        key = (int(getattr(sched, "priority", 0) or 0), occ[0])
        if best_key is None or key > best_key:
            best, best_key = sched, key
    return best


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


def next_schedule_boundary(schedules: list[Any], now_local: datetime) -> datetime | None:
    """The next moment any enabled schedule starts or ends, after ``now_local``.

    Used to expire manual overrides "until the next schedule change"
    (spec §2).  Looks up to 8 days ahead; None when there are no schedules.
    """
    from datetime import timedelta

    best: datetime | None = None
    now = now_local.replace(second=0, microsecond=0)
    for sched in schedules:
        if getattr(sched, "is_enabled", True) is False:
            continue
        start_t = _parse_hhmm(getattr(sched, "start_time", ""))
        if start_t is None:
            continue
        end_raw = getattr(sched, "end_time", None)
        end_t = _parse_hhmm(end_raw) if end_raw else None
        days = set(getattr(sched, "days_of_week", None) or [])
        for offset in range(-1, 8):
            day = (now + timedelta(days=offset)).date()
            if day.weekday() not in days:
                continue
            start_dt = now.replace(
                year=day.year, month=day.month, day=day.day,
                hour=start_t.hour, minute=start_t.minute,
            )
            if end_t is None:
                end_dt = start_dt.replace(hour=0, minute=0) + timedelta(days=1)
            else:
                end_dt = start_dt.replace(hour=end_t.hour, minute=end_t.minute)
                if end_t <= start_t:
                    end_dt += timedelta(days=1)
            for cand in (start_dt, end_dt):
                if cand > now and (best is None or cand < best):
                    best = cand
    return best


# Maximum preconditioning lead (spec §7.3).
MAX_PRECONDITION_LEAD_S = 3 * 3600


def next_start(sched: Any, now_local: datetime) -> datetime | None:
    """The schedule's next occurrence start strictly after ``now_local``.

    Looks at today and the next 7 days, so a 00:30 start is found from
    23:30 the evening before (previously only "today" was searched).
    """
    from datetime import timedelta

    if getattr(sched, "is_enabled", True) is False:
        return None
    start_t = _parse_hhmm(getattr(sched, "start_time", ""))
    if start_t is None:
        return None
    days = set(getattr(sched, "days_of_week", None) or [])
    now = now_local.replace(second=0, microsecond=0)
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        cand = now.replace(
            year=day.year, month=day.month, day=day.day,
            hour=start_t.hour, minute=start_t.minute,
        )
        if cand > now_local:
            return cand
    return None


def seconds_until(later: datetime, now_local: datetime) -> float:
    """Real elapsed seconds (DST-safe: compares instants, not wall clocks)."""
    from datetime import UTC

    return (later.astimezone(UTC) - now_local.astimezone(UTC)).total_seconds()


def upcoming_schedules(
    schedules: list[Any], now_local: datetime, within_s: float = MAX_PRECONDITION_LEAD_S
) -> list[tuple[Any, datetime]]:
    """(schedule, next start) pairs starting within ``within_s``, soonest first."""
    out: list[tuple[Any, datetime]] = []
    for sched in schedules:
        start = next_start(sched, now_local)
        if start is not None and 0 < seconds_until(start, now_local) <= within_s:
            out.append((sched, start))
    out.sort(key=lambda p: (p[1], -int(getattr(p[0], "priority", 0) or 0)))
    return out


def governing_schedule(
    schedules: list[Any],
    now_local: datetime,
    preconditioning: dict[str, datetime],
) -> tuple[Any | None, str | None]:
    """The schedule that governs control right now, and why (spec §3, §7).

    1. The active schedule (``find_active_schedule``), if any — always wins.
    2. Otherwise, in a gap, an upcoming schedule whose preconditioning was
       started (``preconditioning``: schedule id → the occurrence start it
       was started for) and is still valid: that occurrence is still the
       schedule's next start, it is ≤ 3 h away, and the schedule isn't an
       "off" schedule.  The soonest such schedule wins.
    3. Otherwise none.

    The single implementation used by maintenance, Active mode, Follow-Me
    and the schedule runner, so they always agree.
    """
    active = find_active_schedule(schedules, now_local)
    if active is not None:
        return active, "active"
    best: tuple[Any, datetime] | None = None
    for sched in schedules:
        started_for = preconditioning.get(str(getattr(sched, "id", "")))
        if started_for is None:
            continue
        if str(getattr(sched, "hvac_mode", "") or "").lower() == "off":
            continue
        start = next_start(sched, now_local)
        if start is None or start != started_for:
            continue
        if not 0 < seconds_until(start, now_local) <= MAX_PRECONDITION_LEAD_S:
            continue
        if best is None or start < best[1]:
            best = (sched, start)
    if best is not None:
        return best[0], "preconditioning"
    return None, None
