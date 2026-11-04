"""Night-time ceiling on automated fan speed.

Active mode may drive zone fans as extra comfort help, but a ceiling fan at
full speed is loud and drafty once people (and babies) are winding down.
During the configured quiet hours ClimateIQ never drives a fan above
``max_pct``.

The cap only limits what ClimateIQ itself writes.  A fan the user set by
hand is never touched — if their own baseline is above the cap, we simply
don't raise it.

Stored as flat keys in ``system_settings`` (see ``_KV_DEFAULTS`` in
``backend/api/routes/settings.py``):

- ``fan_night_cap_enabled``  (bool)
- ``fan_night_cap_start``    ("HH:MM", local time)
- ``fan_night_cap_end``      ("HH:MM", local time; may wrap past midnight)
- ``fan_night_cap_max_pct``  (0-100)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ENABLED: bool = True
DEFAULT_START: str = "19:00"
DEFAULT_END: str = "07:00"
DEFAULT_MAX_PCT: int = 50

_KEYS = (
    "fan_night_cap_enabled",
    "fan_night_cap_start",
    "fan_night_cap_end",
    "fan_night_cap_max_pct",
)


@dataclass(frozen=True)
class FanNightCap:
    enabled: bool = DEFAULT_ENABLED
    start: str = DEFAULT_START
    end: str = DEFAULT_END
    max_pct: int = DEFAULT_MAX_PCT


def parse_hhmm(raw: Any) -> time | None:
    """Parse 'HH:MM' into a time, or None when malformed."""
    try:
        hh, mm = str(raw).strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def cap_for_time(cfg: FanNightCap, now_t: time) -> int | None:
    """Return the max fan percent allowed at ``now_t``, or None when uncapped.

    The window is start-inclusive, end-exclusive and may wrap past midnight
    (19:00 → 07:00).  start == end means "all day".
    """
    if not cfg.enabled:
        return None
    start_t = parse_hhmm(cfg.start)
    end_t = parse_hhmm(cfg.end)
    if start_t is None or end_t is None:
        return None
    if start_t == end_t:
        in_window = True
    elif start_t < end_t:
        in_window = start_t <= now_t < end_t
    else:
        in_window = now_t >= start_t or now_t < end_t
    if not in_window:
        return None
    return max(0, min(100, int(cfg.max_pct)))


async def load_fan_night_cap(db: Any) -> FanNightCap:
    """Read the quiet-hours fan cap from system_settings, with defaults."""
    if db is None:
        return FanNightCap()
    from sqlalchemy import select as sa_select

    from backend.models.database import SystemSetting

    values: dict[str, Any] = {}
    try:
        result = await db.execute(
            sa_select(SystemSetting).where(SystemSetting.key.in_(_KEYS))
        )
        for row in result.scalars().all():
            values[row.key] = (row.value or {}).get("value")
    except Exception as exc:
        logger.debug("fan_limits: failed to load config (%s) — using defaults", exc)
        return FanNightCap()

    enabled = values.get("fan_night_cap_enabled")
    start = values.get("fan_night_cap_start")
    end = values.get("fan_night_cap_end")
    max_pct = values.get("fan_night_cap_max_pct")
    try:
        max_pct_i = int(max_pct) if max_pct is not None else DEFAULT_MAX_PCT
    except (TypeError, ValueError):
        max_pct_i = DEFAULT_MAX_PCT
    return FanNightCap(
        enabled=bool(enabled) if enabled is not None else DEFAULT_ENABLED,
        start=str(start) if parse_hhmm(start) is not None else DEFAULT_START,
        end=str(end) if parse_hhmm(end) is not None else DEFAULT_END,
        max_pct=max(0, min(100, max_pct_i)),
    )
