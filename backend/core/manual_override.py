"""Manual override: a user-chosen setpoint that pauses automatic control.

Spec §2: a manual override (Dashboard "Set", the dial, chat, quick actions
that set a temperature) pauses *all* automatic setpoint and heat/cool writes
until it expires — at the next schedule boundary, after ``duration_hours``
if given, or on Resume / a system-mode change / emergency shutoff.

Without this, Scheduled mode reverted any manual setpoint within a minute
(climate maintenance, or the drift handler reacting immediately).

Stored in ``system_settings`` (key ``manual_override``) so it survives an
add-on restart.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_KEY = "manual_override"
# With no schedules at all there is no boundary; don't hold forever.
_MAX_HOLD = timedelta(hours=24)


async def _row(db: Any) -> Any:
    from sqlalchemy import select

    from backend.models.database import SystemSetting

    return (
        await db.execute(select(SystemSetting).where(SystemSetting.key == _KEY))
    ).scalar_one_or_none()


async def set_override(
    db: Any,
    setpoint_c: float | None,
    *,
    source: str,
    duration_hours: float | None = None,
    now_local: datetime | None = None,
) -> datetime:
    """Record an override and return its expiry (timezone-aware)."""
    from sqlalchemy import select

    from backend.core.schedule_guard import next_schedule_boundary
    from backend.models.database import Schedule, SystemSetting

    if now_local is None:
        import backend.api.dependencies as deps
        from backend.core.user_tz import local_now

        now_local = await local_now(db, deps._ha_client)
    if duration_hours is not None and float(duration_hours) > 0:
        expires = now_local + timedelta(hours=float(duration_hours))
    else:
        schedules = list(
            (await db.execute(select(Schedule).where(Schedule.is_enabled.is_(True))))
            .scalars()
            .all()
        )
        boundary = next_schedule_boundary(schedules, now_local)
        expires = boundary if boundary is not None else now_local + _MAX_HOLD
        expires = min(expires, now_local + _MAX_HOLD)
    value = {
        "setpoint_c": None if setpoint_c is None else float(setpoint_c),
        "set_at": datetime.now(UTC).isoformat(),
        "expires_at": expires.astimezone(UTC).isoformat(),
        "source": source,
    }
    row = await _row(db)
    if row is None:
        db.add(SystemSetting(key=_KEY, value={"value": value}))
    else:
        row.value = {"value": value}
    await db.commit()
    logger.info(
        "Manual override (%s) from %s until %s",
        "preset" if setpoint_c is None else f"{setpoint_c:.1f}°C", source, expires.isoformat(),
    )
    return expires


async def get_active_override(db: Any) -> dict[str, Any] | None:
    """The current override, or None (an expired one is cleared)."""
    row = await _row(db)
    if row is None or not row.value:
        return None
    value = (row.value or {}).get("value")
    if not isinstance(value, dict):
        return None
    try:
        expires = datetime.fromisoformat(str(value.get("expires_at")))
    except ValueError:
        expires = datetime.now(UTC)
    if expires <= datetime.now(UTC):
        await clear_override(db, reason="expired")
        return None
    return value


async def clear_override(db: Any, *, reason: str) -> None:
    row = await _row(db)
    if row is not None:
        await db.delete(row)
        await db.commit()
        logger.info("Manual override cleared (%s)", reason)
