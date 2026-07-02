"""Seasonal HVAC mode lock with outdoor-temperature safety override.

The user configures a set of seasons by month/day boundaries.  Each season
declares a preferred HVAC direction (``heat`` / ``cool`` / ``auto``).  Each
season can also declare an outdoor-temperature *escape valve* — e.g. "summer
locks to cool, BUT allow heat if outdoor drops below 40°F".

Other parts of the control loop call :func:`compute_locked_mode` to ask
"given today's date and the current outdoor temp, which mode (if any) is
locked?"  Returning ``None`` means no lock — fall back to the existing
sensor-driven mode selection.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


ModePref = Literal["auto", "heat", "cool"]


class Season(BaseModel):
    """A single season range and its preferred HVAC behaviour."""

    name: str = Field(min_length=1, max_length=40)
    start_month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)
    end_month: int = Field(ge=1, le=12)
    end_day: int = Field(ge=1, le=31)
    preferred_mode: ModePref = "auto"
    # Cool season override: allow heat when outdoor temp ≤ this (°C).
    override_outdoor_below_c: float | None = None
    # Heat season override: allow cool when outdoor temp ≥ this (°C).
    override_outdoor_above_c: float | None = None

    @field_validator("preferred_mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.lower().strip()
        return v


class SeasonalLockConfig(BaseModel):
    enabled: bool = False
    seasons: list[Season] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SEASONS: list[dict[str, Any]] = [
    {
        "name": "Winter",
        "start_month": 12, "start_day": 1,
        "end_month": 2, "end_day": 28,
        "preferred_mode": "heat",
        # 21°C ≈ 70°F — if outdoors warmer than that, allow cool
        "override_outdoor_above_c": 21.1,
    },
    {
        "name": "Spring",
        "start_month": 3, "start_day": 1,
        "end_month": 4, "end_day": 30,
        "preferred_mode": "auto",
    },
    {
        "name": "Summer",
        "start_month": 5, "start_day": 1,
        "end_month": 9, "end_day": 30,
        "preferred_mode": "cool",
        # 4.4°C ≈ 40°F — if outdoors colder than that, allow heat
        "override_outdoor_below_c": 4.4,
    },
    {
        "name": "Fall",
        "start_month": 10, "start_day": 1,
        "end_month": 11, "end_day": 30,
        "preferred_mode": "auto",
    },
]

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "seasons": DEFAULT_SEASONS,
}


# ---------------------------------------------------------------------------
# Season detection
# ---------------------------------------------------------------------------


def _season_contains(season: Season, month: int, day: int) -> bool:
    """Return True if (month, day) falls inside the given season range.

    Handles year-wrap (e.g. Dec 1 → Feb 28).
    """
    start = (season.start_month, season.start_day)
    end = (season.end_month, season.end_day)
    cur = (month, day)
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end


def find_active_season(seasons: list[Season], today: date) -> Season | None:
    """Return the first season whose range contains *today*, or None."""
    for s in seasons:
        if _season_contains(s, today.month, today.day):
            return s
    return None


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


async def load_config(db: Any) -> SeasonalLockConfig:
    """Load the persisted seasonal lock config from system_settings."""
    if db is None:
        return SeasonalLockConfig(**DEFAULT_CONFIG)
    from sqlalchemy import select as sa_select

    from backend.models.database import SystemSetting

    try:
        result = await db.execute(
            sa_select(SystemSetting).where(SystemSetting.key == "seasonal_lock")
        )
        row = result.scalar_one_or_none()
        if row and row.value:
            raw = row.value
            if isinstance(raw, dict) and "value" in raw:
                raw = raw["value"]
            if isinstance(raw, dict):
                return SeasonalLockConfig.model_validate(raw)
    except Exception as exc:
        logger.debug("seasonal_lock: failed to load config (%s) — using defaults", exc)
    return SeasonalLockConfig(**DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Outdoor temperature reader (mirrors climate_advisor pattern)
# ---------------------------------------------------------------------------


async def _read_weather_entity(db: Any) -> str | None:
    if db is None:
        return None
    from sqlalchemy import select as sa_select

    from backend.models.database import SystemSetting

    try:
        result = await db.execute(
            sa_select(SystemSetting).where(SystemSetting.key == "weather_entity")
        )
        row = result.scalar_one_or_none()
        if row and row.value:
            val = row.value
            if isinstance(val, dict):
                val = val.get("value", "")
            return str(val) if val else None
    except Exception:  # noqa: S110
        pass
    return None


async def _read_outdoor_temp_c(db: Any, ha_client: Any) -> float | None:
    """Read outdoor temperature in °C from the configured weather entity.

    Returns None when no entity is configured, HA is unreachable, or the
    attribute is missing.
    """
    entity = await _read_weather_entity(db)
    if not entity or ha_client is None:
        return None
    try:
        state = await ha_client.get_state(entity)
        if not state:
            return None
        temp = state.attributes.get("temperature")
        if isinstance(temp, (int, float)) and not isinstance(temp, bool):
            return float(temp)
    except Exception as exc:
        logger.debug("seasonal_lock: outdoor read failed (%s)", exc)
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


class SeasonalLockState(BaseModel):
    """Computed state — what the lock currently demands."""

    enabled: bool
    active_season: str | None = None
    preferred_mode: ModePref | None = None
    locked_mode: Literal["heat", "cool"] | None = None
    outdoor_temp_c: float | None = None
    override_active: bool = False
    reason: str = ""


async def compute_lock_state(
    db: Any,
    ha_client: Any,
    *,
    now: date | None = None,
) -> SeasonalLockState:
    """Return the full computed seasonal lock state for the current moment."""
    cfg = await load_config(db)
    state = SeasonalLockState(enabled=cfg.enabled)
    if not cfg.enabled:
        state.reason = "seasonal lock disabled"
        return state

    today = now or date.today()
    season = find_active_season(cfg.seasons, today)
    if season is None:
        state.reason = "no active season for today"
        return state

    state.active_season = season.name
    state.preferred_mode = season.preferred_mode

    if season.preferred_mode == "auto":
        state.reason = f"season '{season.name}' has no lock"
        return state

    # Determine whether the outdoor override has tripped.
    outdoor_c = await _read_outdoor_temp_c(db, ha_client)
    state.outdoor_temp_c = outdoor_c

    if outdoor_c is not None:
        if (
            season.preferred_mode == "cool"
            and season.override_outdoor_below_c is not None
            and outdoor_c <= season.override_outdoor_below_c
        ):
            state.override_active = True
            state.reason = (
                f"override: outdoor {outdoor_c:.1f}°C ≤ "
                f"{season.override_outdoor_below_c:.1f}°C — heat allowed"
            )
            return state
        if (
            season.preferred_mode == "heat"
            and season.override_outdoor_above_c is not None
            and outdoor_c >= season.override_outdoor_above_c
        ):
            state.override_active = True
            state.reason = (
                f"override: outdoor {outdoor_c:.1f}°C ≥ "
                f"{season.override_outdoor_above_c:.1f}°C — cool allowed"
            )
            return state

    # Lock is in effect.
    state.locked_mode = season.preferred_mode
    state.reason = f"season '{season.name}': locked to {season.preferred_mode}"
    return state


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_SEASONS",
    "ModePref",
    "Season",
    "SeasonalLockConfig",
    "SeasonalLockState",
    "compute_lock_state",
    "find_active_season",
    "load_config",
]
