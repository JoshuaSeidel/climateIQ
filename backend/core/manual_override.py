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


async def _override_expiry(db: Any, value: dict[str, Any]) -> datetime:
    """When an override ends — computed from the *current* schedules.

    Spec §2: the next schedule boundary after it was set (so adding/editing
    a schedule moves the end), or ``duration_hours`` if given, never more
    than 24 h.  Durations are real elapsed time (DST-safe).
    """
    from sqlalchemy import select

    import backend.api.dependencies as deps
    from backend.core.schedule_guard import next_schedule_boundary
    from backend.core.user_tz import resolve_user_tz
    from backend.models.database import Schedule

    set_at = datetime.fromisoformat(str(value.get("set_at")))
    cap = set_at + _MAX_HOLD
    dur = value.get("duration_hours")
    if dur is not None:
        return min(set_at + timedelta(hours=float(dur)), cap)
    tz = await resolve_user_tz(db, deps._ha_client)
    schedules = list(
        (await db.execute(select(Schedule).where(Schedule.is_enabled.is_(True)))).scalars().all()
    )
    boundary = next_schedule_boundary(schedules, set_at.astimezone(tz))
    return cap if boundary is None else min(boundary.astimezone(UTC), cap)


_EXPIRED_KEY = "override_expired_handback"


def _reset_gap_handback() -> None:
    """Any override change re-arms the Scheduled-mode gap handback (spec §2)."""
    try:
        import backend.api.main as main_mod

        main_mod._gap_handed_back = False
    except Exception:  # noqa: S110
        pass


async def mark_expired_handback(db: Any) -> None:
    """Re-arm the Learn expiry handback (a resume attempt failed)."""
    from sqlalchemy import select

    from backend.models.database import SystemSetting

    existing = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == _EXPIRED_KEY))
    ).scalar_one_or_none()
    if existing is None:
        db.add(SystemSetting(key=_EXPIRED_KEY, value={"value": True}))
        await db.commit()


async def pop_expired_handback(db: Any) -> bool:
    """True once after an override expired (Learn hands back to the Ecobee)."""
    from sqlalchemy import select

    from backend.models.database import SystemSetting

    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == _EXPIRED_KEY))
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def set_override(
    db: Any,
    setpoint_c: float | None,
    *,
    source: str,
    duration_hours: float | None = None,
    now_local: datetime | None = None,
) -> datetime:
    """Record an override and return its expiry (timezone-aware)."""
    from backend.models.database import SystemSetting

    value: dict[str, Any] = {
        "setpoint_c": None if setpoint_c is None else float(setpoint_c),
        "set_at": (now_local or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "duration_hours": (
            None if duration_hours is None else min(float(duration_hours), 24.0)
        ),
        "source": source,
    }
    expires = await _override_expiry(db, value)
    value["expires_at"] = expires.isoformat()  # informational; recomputed on read
    _reset_gap_handback()
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
    """The current override (with a fresh ``expires_at``), or None.

    An expired override is removed — but only if it is still the same
    override (``set_at`` unchanged), so a new one the user set at that
    moment is never deleted by this cleanup.
    """
    row = await _row(db)
    if row is None or not row.value:
        return None
    value = (row.value or {}).get("value")
    if not isinstance(value, dict) or not value.get("set_at"):
        return None
    try:
        expires = await _override_expiry(db, value)
    except Exception as exc:
        logger.debug("Override expiry computation failed: %s", exc)
        return value
    if expires <= datetime.now(UTC):
        await clear_override(db, reason="expired", expected_set_at=str(value.get("set_at")))
        return None
    return {**value, "expires_at": expires.isoformat()}


async def clear_override(db: Any, *, reason: str, expected_set_at: str | None = None) -> None:
    """Remove the override.  With ``expected_set_at``, only if it still matches."""
    from sqlalchemy import select

    from backend.models.database import SystemSetting

    row = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.key == _KEY).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await db.commit()
        return
    if expected_set_at is not None:
        current = ((row.value or {}).get("value") or {}).get("set_at")
        if str(current) != expected_set_at:
            await db.commit()  # replaced meanwhile — keep the new override
            return
    await db.delete(row)
    if reason == "expired":
        # Nothing else notices an expiry in Learn (every loop stands down);
        # leave a marker so maintenance hands the hold back to the Ecobee.
        existing = (
            await db.execute(select(SystemSetting).where(SystemSetting.key == _EXPIRED_KEY))
        ).scalar_one_or_none()
        if existing is None:
            db.add(SystemSetting(key=_EXPIRED_KEY, value={"value": True}))
    await db.commit()
    _reset_gap_handback()
    logger.info("Manual override cleared (%s)", reason)


async def snapshot_override(db: Any) -> dict[str, Any] | None:
    """The raw stored override (for restoring if a new one fails)."""
    row = await _row(db)
    if row is None or not row.value:
        return None
    return dict(row.value)


async def restore_override(db: Any, snapshot: dict[str, Any] | None, *, reason: str) -> None:
    """Put back the override that was in place before a failed request.

    A rejected or failed manual request must change nothing (spec §2) — it
    used to delete the override that was already running.
    """
    from backend.models.database import SystemSetting

    if snapshot is None:
        await clear_override(db, reason=reason)
        return
    row = await _row(db)
    if row is None:
        db.add(SystemSetting(key=_KEY, value=snapshot))
    else:
        row.value = snapshot
    await db.commit()
    _reset_gap_handback()
    logger.info("Manual override restored (%s)", reason)
