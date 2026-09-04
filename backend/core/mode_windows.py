"""Time-of-day HVAC direction windows.

Seasonal lock answers "which direction may run *this month*".  This module
answers the finer question "which direction may run *right now*".

The motivating case: it is still technically summer, but the nights turn
cold.  The user wants heat available overnight to hold the schedule target,
and never during the day — regardless of what the zone sensors ask for.

Two layers, resolved in this order:

1. **Per-season windows** — when the seasonal lock is enabled and today falls
   inside a season that declares its own ``windows``, those windows are used.
   This lets the day/night rules change automatically as the year turns.
2. **Standalone windows** — the base layer, always in effect when enabled.
   Used whenever layer 1 does not apply, so the feature works with the
   seasonal lock switched off entirely.

A window that matches the current local time decides which directions are
permitted.  When no window matches, both directions are allowed — windows
are restrictions, never grants, so an incomplete schedule can never leave
the house with no HVAC at all.

Each window carries an optional temperature escape valve so a block can
never let the house run away: "no heat during the day, *unless* the rooms
fall below 60°F".

Enforcement lives in ``backend/api/main.py``:
  - :func:`is_mode_allowed` gates every thermostat mode switch.
  - :func:`clamp_setpoint_for_blocked_direction` keeps the HVAC idle when the
    thermostat is *already* sitting in a now-blocked mode, without forcing a
    mode change.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# 1°F in Celsius — the smallest step a thermostat actually acts on.
_ONE_DEGREE_F_C = 5.0 / 9.0


class TimeWindow(BaseModel):
    """A time-of-day range and the HVAC directions permitted inside it."""

    name: str = Field(default="", max_length=40)
    start_time: str = "00:00"
    end_time: str = "00:00"
    # Empty list = every day.  0 = Monday … 6 = Sunday (matches Schedule).
    days_of_week: list[int] = Field(default_factory=list)
    allow_heat: bool = True
    allow_cool: bool = True
    # Escape valves — evaluated against the zone average, not the thermostat.
    # Allow heat anyway when zones fall to/below this (°C).
    escape_below_c: float | None = None
    # Allow cool anyway when zones rise to/above this (°C).
    escape_above_c: float | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_hhmm(cls, v: Any) -> str:
        raw = str(v or "").strip()
        try:
            hh, mm = raw.split(":")
            parsed = time(int(hh), int(mm))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"time must be 'HH:MM', got {v!r}") from exc
        return f"{parsed.hour:02d}:{parsed.minute:02d}"

    @field_validator("days_of_week")
    @classmethod
    def _validate_dow(cls, v: Any) -> list[int]:
        if not v:
            return []
        days = sorted({int(d) for d in v})
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("days_of_week entries must be 0-6 (Mon-Sun)")
        return days

    def as_times(self) -> tuple[time, time]:
        s_h, s_m = (int(x) for x in self.start_time.split(":"))
        e_h, e_m = (int(x) for x in self.end_time.split(":"))
        return time(s_h, s_m), time(e_h, e_m)


class ModeWindowConfig(BaseModel):
    enabled: bool = False
    windows: list[TimeWindow] = Field(default_factory=list)


class ModeWindowState(BaseModel):
    """What the windows permit at this moment."""

    enabled: bool = False
    allow_heat: bool = True
    allow_cool: bool = True
    active_window: str | None = None
    # "standalone", "season:<name>", or "" when no window applies.
    source: str = ""
    escape_active: bool = False
    zone_avg_c: float | None = None
    local_time: str = ""
    reason: str = "no time windows in effect"

    def allows(self, mode: str) -> bool:
        m = (mode or "").lower()
        if "heat" in m and "cool" not in m:
            return self.allow_heat
        if "cool" in m and "heat" not in m:
            return self.allow_cool
        # "off", "heat_cool", "auto", "" — not a single direction, never blocked.
        return True


DEFAULT_CONFIG: dict[str, Any] = {"enabled": False, "windows": []}


# ---------------------------------------------------------------------------
# Window matching
# ---------------------------------------------------------------------------


def window_matches(window: TimeWindow, now: datetime) -> bool:
    """Return True when *now* (local) falls inside the window.

    Handles ranges that wrap past midnight (e.g. 21:00 → 07:00).  For a
    wrapping window the ``days_of_week`` filter is applied to the day the
    window *started* — an overnight Friday window still covers 02:00 on
    Saturday morning.
    """
    start_t, end_t = window.as_times()
    cur_t = now.time()
    dow = now.weekday()

    if start_t == end_t:
        # Zero-length range is meaningless; treat as "all day".
        in_range, start_dow = True, dow
    elif start_t < end_t:
        in_range, start_dow = (start_t <= cur_t < end_t), dow
    else:
        # Wraps midnight: either late today, or early on the following day.
        if cur_t >= start_t:
            in_range, start_dow = True, dow
        elif cur_t < end_t:
            in_range, start_dow = True, (dow - 1) % 7
        else:
            in_range, start_dow = False, dow

    if not in_range:
        return False
    if window.days_of_week and start_dow not in window.days_of_week:
        return False
    return True


def find_active_window(windows: list[TimeWindow], now: datetime) -> TimeWindow | None:
    """Return the first window covering *now*, or None."""
    for w in windows:
        if window_matches(w, now):
            return w
    return None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


async def load_config(db: Any) -> ModeWindowConfig:
    """Load the standalone window config from system_settings."""
    if db is None:
        return ModeWindowConfig(**DEFAULT_CONFIG)
    from sqlalchemy import select as sa_select

    from backend.models.database import SystemSetting

    try:
        result = await db.execute(
            sa_select(SystemSetting).where(SystemSetting.key == "hvac_time_windows")
        )
        row = result.scalar_one_or_none()
        if row and row.value:
            raw = row.value
            if isinstance(raw, dict) and "value" in raw:
                raw = raw["value"]
            if isinstance(raw, dict):
                return ModeWindowConfig.model_validate(raw)
    except Exception as exc:
        logger.debug("mode_windows: failed to load config (%s) — using defaults", exc)
    return ModeWindowConfig(**DEFAULT_CONFIG)


async def get_local_now(db: Any, ha_client: Any) -> datetime:
    """Resolve 'now' in the user's configured timezone.

    Mirrors the resolution order used by the climate maintenance loop: the
    ``timezone`` system setting first, then Home Assistant's own timezone,
    then UTC.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("UTC")
    if db is not None:
        try:
            from sqlalchemy import select as sa_select

            from backend.models.database import SystemSetting

            result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "timezone")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                val = row.value.get("value", "") if isinstance(row.value, dict) else str(row.value)
                if val:
                    tz = ZoneInfo(val)
        except Exception:  # noqa: S110
            pass
    if str(tz) == "UTC" and ha_client is not None:
        try:
            ha_config = await ha_client.get_config()
            ha_tz = ha_config.get("time_zone", "")
            if ha_tz:
                tz = ZoneInfo(ha_tz)
        except Exception:  # noqa: S110
            pass
    return datetime.now(tz)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def compute_window_state(
    db: Any,
    ha_client: Any,
    *,
    zone_avg_c: float | None = None,
    zone_ids: list[Any] | None = None,
    now: datetime | None = None,
) -> ModeWindowState:
    """Resolve which HVAC directions are permitted at this moment.

    ``zone_avg_c`` short-circuits the sensor read when the caller already has
    the zone average in hand (the maintenance loop always does).  It is only
    fetched lazily when a window actually blocks a direction *and* declares an
    escape valve, so the common path costs no extra HA calls.
    """
    local_now = now or await get_local_now(db, ha_client)
    state = ModeWindowState(local_time=local_now.strftime("%Y-%m-%d %H:%M %Z").strip())

    windows, source = await _resolve_windows(db, ha_client, local_now)
    if not windows:
        state.reason = "no time windows configured"
        return state

    state.enabled = True
    window = find_active_window(windows, local_now)
    if window is None:
        state.source = source
        state.reason = f"no {source} window covers {local_now.strftime('%H:%M')}"
        return state

    label = window.name or f"{window.start_time}-{window.end_time}"
    state.active_window = label
    state.source = source
    state.allow_heat = window.allow_heat
    state.allow_cool = window.allow_cool

    # Escape valve — only worth a sensor read when something is actually blocked.
    needs_escape = (
        (not window.allow_heat and window.escape_below_c is not None)
        or (not window.allow_cool and window.escape_above_c is not None)
    )
    if needs_escape:
        avg_c = zone_avg_c
        if avg_c is None:
            avg_c = await _read_zone_avg_c(db, ha_client, zone_ids)
        state.zone_avg_c = avg_c
        if avg_c is not None:
            if (
                not window.allow_heat
                and window.escape_below_c is not None
                and avg_c <= window.escape_below_c
            ):
                state.allow_heat = True
                state.escape_active = True
                state.reason = (
                    f"window '{label}' blocks heat, but zones "
                    f"{_f(avg_c):.1f}°F ≤ {_f(window.escape_below_c):.1f}°F "
                    f"— heat allowed"
                )
                return state
            if (
                not window.allow_cool
                and window.escape_above_c is not None
                and avg_c >= window.escape_above_c
            ):
                state.allow_cool = True
                state.escape_active = True
                state.reason = (
                    f"window '{label}' blocks cool, but zones "
                    f"{_f(avg_c):.1f}°F ≥ {_f(window.escape_above_c):.1f}°F "
                    f"— cool allowed"
                )
                return state

    allowed = [
        d for d, ok in (("heat", state.allow_heat), ("cool", state.allow_cool)) if ok
    ]
    state.reason = (
        f"{source} window '{label}': "
        + (f"{' + '.join(allowed)} allowed" if allowed else "no HVAC allowed")
    )
    return state


async def _resolve_windows(
    db: Any, ha_client: Any, local_now: datetime
) -> tuple[list[TimeWindow], str]:
    """Pick the effective window list: per-season overrides beat standalone."""
    # Layer 1: per-season windows, when the seasonal lock is on and today
    # falls inside a season that declares its own.
    try:
        from backend.core.seasonal_lock import find_active_season
        from backend.core.seasonal_lock import load_config as load_season_config

        season_cfg = await load_season_config(db)
        if season_cfg.enabled:
            season = find_active_season(season_cfg.seasons, local_now.date())
            if season is not None and season.windows:
                return list(season.windows), f"season:{season.name}"
    except Exception as exc:
        logger.debug("mode_windows: seasonal override lookup failed (%s)", exc)

    # Layer 2: standalone windows.
    cfg = await load_config(db)
    if cfg.enabled and cfg.windows:
        return list(cfg.windows), "standalone"
    return [], ""


async def _read_zone_avg_c(
    db: Any, ha_client: Any, zone_ids: list[Any] | None
) -> float | None:
    try:
        from backend.core.temp_compensation import get_avg_zone_temp_c

        ids = [str(z) for z in zone_ids] if zone_ids else None
        avg_c, _ = await get_avg_zone_temp_c(db, ids, ha_client=ha_client)
        return avg_c
    except Exception as exc:
        logger.debug("mode_windows: zone average read failed (%s)", exc)
        return None


async def is_mode_allowed(
    db: Any,
    ha_client: Any,
    mode: str,
    *,
    zone_avg_c: float | None = None,
    zone_ids: list[Any] | None = None,
    now: datetime | None = None,
) -> tuple[bool, ModeWindowState]:
    """Return ``(allowed, state)`` for a single HVAC direction.

    Fails open: any error resolving the windows leaves the mode allowed, so a
    bad config can never wedge the thermostat.
    """
    try:
        state = await compute_window_state(
            db, ha_client, zone_avg_c=zone_avg_c, zone_ids=zone_ids, now=now
        )
    except Exception as exc:
        logger.debug("mode_windows: state computation failed (%s) — allowing", exc)
        return True, ModeWindowState(reason=f"window check failed: {exc}")
    return state.allows(mode), state


def satisfied_setpoint_c(
    blocked_mode: str,
    desired_temp_c: float,
    thermostat_c: float | None,
) -> float:
    """Return a setpoint that leaves the HVAC idle in ``blocked_mode``.

    The thermostat compares its setpoint against its own sensor: heat fires
    only while reading < setpoint, cool only while reading > setpoint.  To
    park it without a mode change, put the setpoint one whole °F on the
    satisfied side of the current reading.  Falls back to the schedule target
    when the reading is unavailable.
    """
    m = (blocked_mode or "").lower()
    if "heat" in m:
        if thermostat_c is None:
            return desired_temp_c
        return min(desired_temp_c, thermostat_c - _ONE_DEGREE_F_C)
    if "cool" in m:
        if thermostat_c is None:
            return desired_temp_c
        return max(desired_temp_c, thermostat_c + _ONE_DEGREE_F_C)
    return desired_temp_c


async def clamp_setpoint_for_blocked_direction(
    db: Any,
    ha_client: Any,
    climate_entity: str,
    setpoint_c: float,
    desired_temp_c: float,
    *,
    hvac_mode: str | None = None,
    thermostat_c: float | None = None,
    zone_avg_c: float | None = None,
    zone_ids: list[Any] | None = None,
    context: str = "",
) -> float:
    """Park the setpoint when the thermostat sits in a blocked direction.

    Returns ``setpoint_c`` unchanged when the direction is permitted (the
    overwhelmingly common case) or when the mode is not a single direction.
    Otherwise returns a value the thermostat has already satisfied, so the
    HVAC idles without ClimateIQ forcing a mode change.

    ``hvac_mode`` and ``thermostat_c`` are read from Home Assistant when the
    caller does not already have them.  Fails open on any error.
    """
    try:
        from backend.core.temp_compensation import (
            _get_hvac_mode,
            get_thermostat_reading_c,
        )

        mode = (hvac_mode or "").lower()
        if not mode:
            mode = (await _get_hvac_mode(ha_client, climate_entity)).lower()
        if not mode or ("heat" in mode) == ("cool" in mode):
            return setpoint_c  # off / heat_cool / auto / unknown — nothing to block

        allowed, state = await is_mode_allowed(
            db, ha_client, mode, zone_avg_c=zone_avg_c, zone_ids=zone_ids
        )
        if allowed:
            return setpoint_c

        if thermostat_c is None:
            thermostat_c = await get_thermostat_reading_c(
                ha_client, climate_entity, db=db
            )

        parked_c = satisfied_setpoint_c(mode, desired_temp_c, thermostat_c)
        if abs(parked_c - setpoint_c) < 0.01:
            return setpoint_c
        logger.info(
            "%s%s blocked by time window (%s) — setpoint %.1f°F parked at %.1f°F "
            "so the HVAC idles without a mode change",
            f"{context}: " if context else "",
            mode,
            state.reason,
            _f(setpoint_c),
            _f(parked_c),
        )
        return parked_c
    except Exception as exc:
        logger.debug("mode_windows: setpoint clamp failed (%s) — leaving as-is", exc)
        return setpoint_c


def _f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


__all__ = [
    "DEFAULT_CONFIG",
    "ModeWindowConfig",
    "ModeWindowState",
    "TimeWindow",
    "clamp_setpoint_for_blocked_direction",
    "compute_window_state",
    "find_active_window",
    "get_local_now",
    "is_mode_allowed",
    "load_config",
    "satisfied_setpoint_c",
    "window_matches",
]
