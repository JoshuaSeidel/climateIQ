"""Single source of truth for the user's local timezone.

Schedules, time windows, seasons and quiet hours are all expressed in local
wall-clock time, so every loop must agree on what "now" is.  Previously each
caller resolved the timezone itself and they disagreed: some fell back to
Home Assistant's timezone, Active mode did not, and an invalid stored value
(e.g. ``America\\New_York`` with a backslash) silently became UTC — five
hours off for a US-Eastern home.

Resolution order:
1. ``system_settings.timezone`` — normalised (backslashes → slashes) and
   validated.  ``"UTC"`` is treated as "unset" because it is the settings
   default and gets written back by every Settings save.
2. Home Assistant's configured ``time_zone``.
3. UTC.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")
# Last timezone successfully read from Home Assistant.  A single failed or
# slow /api/config call must not silently switch a tick to UTC (≈5 h off in
# US-Eastern — enough to fire the wrong schedule).
_last_ha_tz: ZoneInfo | None = None


def parse_tz(value: Any) -> ZoneInfo | None:
    """Parse a stored timezone value; None when empty, UTC, or invalid."""
    if isinstance(value, dict):
        value = value.get("value", "")
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.upper() in ("UTC", "ETC/UTC"):
        return None
    try:
        return ZoneInfo(raw)
    except Exception:
        logger.warning("Ignoring invalid timezone setting %r", value)
        return None


async def resolve_user_tz(db: Any, ha_client: Any) -> ZoneInfo:
    """Return the user's timezone (see module docstring for the order)."""
    if db is not None:
        try:
            from sqlalchemy import select as sa_select

            from backend.models.database import SystemSetting

            result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "timezone")
            )
            row = result.scalar_one_or_none()
            if row is not None and row.value:
                tz = parse_tz(row.value)
                if tz is not None:
                    return tz
        except Exception as exc:
            logger.debug("user_tz: settings lookup failed (%s)", exc)
    global _last_ha_tz
    if ha_client is not None:
        try:
            ha_config = await ha_client.get_config()
            tz = parse_tz(ha_config.get("time_zone", ""))
            if tz is not None:
                if _last_ha_tz is None or str(_last_ha_tz) != str(tz):
                    await _persist_ha_tz(db, str(tz))
                _last_ha_tz = tz
                return tz
        except Exception as exc:
            logger.debug("user_tz: HA config lookup failed (%s)", exc)
    if _last_ha_tz is not None:
        return _last_ha_tz
    # After a restart: the last HA timezone we saw, persisted — never
    # silently UTC (spec §3.5).
    cached = await _load_ha_tz(db)
    if cached is not None:
        _last_ha_tz = cached
        return cached
    logger.warning("user_tz: no timezone available — using UTC")
    return _UTC


_CACHE_KEY = "ha_timezone_cache"


async def _persist_ha_tz(db: Any, name: str) -> None:
    if db is None:
        return
    try:
        from sqlalchemy import select as sa_select

        from backend.models.database import SystemSetting

        row = (
            await db.execute(sa_select(SystemSetting).where(SystemSetting.key == _CACHE_KEY))
        ).scalar_one_or_none()
        if row is None:
            db.add(SystemSetting(key=_CACHE_KEY, value={"value": name}))
        else:
            row.value = {"value": name}
        await db.commit()
    except Exception as exc:
        logger.debug("user_tz: could not persist HA timezone (%s)", exc)


async def _load_ha_tz(db: Any) -> ZoneInfo | None:
    if db is None:
        return None
    try:
        from sqlalchemy import select as sa_select

        from backend.models.database import SystemSetting

        row = (
            await db.execute(sa_select(SystemSetting).where(SystemSetting.key == _CACHE_KEY))
        ).scalar_one_or_none()
        return parse_tz(row.value) if row is not None and row.value else None
    except Exception:
        return None


async def local_now(db: Any, ha_client: Any) -> datetime:
    """Timezone-aware 'now' in the user's local time."""
    return datetime.now(await resolve_user_tz(db, ha_client))
