"""Schedule API routes for ClimateIQ."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.models.database import Schedule, Zone

router = APIRouter()


_log = __import__("logging").getLogger(__name__)
_bg_tasks: set[asyncio.Task[None]] = set()


def _fire_apply_now(schedule: Schedule) -> None:
    """Create a background task for immediate schedule application.

    Keeps a reference in _bg_tasks so the event loop doesn't GC the task
    before it completes (satisfies RUF006).
    """
    task = asyncio.create_task(_apply_schedule_now_bg(schedule))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _apply_schedule_now_bg(schedule: Schedule) -> None:
    """Fire-and-forget wrapper: immediately apply a schedule after it is saved.

    Uses a lazy import of apply_schedule_now from main to avoid circular
    imports at module load time.
    """
    try:
        from backend.api.main import apply_schedule_now  # lazy import — safe at call time

        await apply_schedule_now(schedule)
    except Exception as _err:
        _log.debug("_apply_schedule_now_bg (non-critical): %s", _err)


# ============================================================================
# Pydantic Models
# ============================================================================


# Real 24-hour HH:MM — the old ``^\d{2}:\d{2}$`` accepted "24:00" and
# "99:99", which committed and then made every schedule listing 500.
_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


def _normalise_hvac_mode(v: str) -> str:
    """Trim/lower and validate — "OFF " used to slip past every "off" check."""
    mode = str(v or "").strip().lower()
    aliases = {"heating": "heat", "cooling": "cool", "heat_cool": "auto"}
    mode = aliases.get(mode, mode)
    if mode not in ("auto", "heat", "cool", "off"):
        raise ValueError("hvac_mode must be one of: auto, heat, cool, off")
    return mode


def _validate_days_list(v: list[int]) -> list[int]:
    if not v:
        raise ValueError("At least one day must be selected")
    for day in v:
        if day < 0 or day > 6:
            raise ValueError("Days must be between 0 (Monday) and 6 (Sunday)")
    return sorted(set(v))


class ScheduleCreate(BaseModel):
    """Schedule creation request."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=100)
    zone_ids: list[uuid.UUID] = Field(default_factory=list)  # empty = all zones
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon, 6=Sun
    start_time: str = Field(..., pattern=_HHMM)  # HH:MM format
    end_time: str | None = Field(None, pattern=_HHMM)
    target_temp_c: float = Field(..., ge=4.4, le=37.8)
    hvac_mode: str = Field(default="auto")  # auto, heat, cool, off
    is_enabled: bool = True
    priority: int = Field(default=1, ge=1, le=10)  # Higher = more important

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: list[int]) -> list[int]:
        return _validate_days_list(v)

    @field_validator("hvac_mode")
    @classmethod
    def validate_hvac_mode(cls, v: str) -> str:
        return _normalise_hvac_mode(v)

    @field_validator("target_temp_c")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """Enforce safety temperature bounds."""
        if v < 4.4 or v > 37.8:
            raise ValueError(
                f"Temperature {v}°C is outside safety bounds (4.4°C - 37.8°C / 40°F - 100°F)"
            )
        return v


class ScheduleUpdate(BaseModel):
    """Schedule update request."""

    model_config = {"extra": "ignore"}

    name: str | None = Field(None, min_length=1, max_length=100)
    zone_ids: list[uuid.UUID] | None = None
    days_of_week: list[int] | None = None
    start_time: str | None = Field(None, pattern=_HHMM)
    end_time: str | None = Field(None, pattern=_HHMM)
    target_temp_c: float | None = Field(None, ge=4.4, le=37.8)
    hvac_mode: str | None = None
    is_enabled: bool | None = None
    priority: int | None = Field(None, ge=1, le=10)

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: list[int] | None) -> list[int] | None:
        return None if v is None else _validate_days_list(v)

    @field_validator("hvac_mode")
    @classmethod
    def validate_hvac_mode(cls, v: str | None) -> str | None:
        return None if v is None else _normalise_hvac_mode(v)


class ScheduleResponse(BaseModel):
    """Schedule response."""

    id: uuid.UUID
    name: str
    zone_ids: list[uuid.UUID] = []
    zone_names: list[str] = []
    days_of_week: list[int]
    start_time: str
    end_time: str | None
    target_temp_c: float
    hvac_mode: str
    is_enabled: bool
    priority: int
    created_at: datetime
    updated_at: datetime
    next_occurrence: datetime | None = None


class UpcomingSchedule(BaseModel):
    """Upcoming schedule occurrence."""

    schedule_id: uuid.UUID
    schedule_name: str
    zone_ids: list[uuid.UUID] = []
    zone_names: list[str] = []
    start_time: datetime
    end_time: datetime | None
    target_temp_c: float
    hvac_mode: str


class ScheduleConflict(BaseModel):
    """Schedule conflict information."""

    schedule_id: uuid.UUID
    schedule_name: str
    conflict_type: str  # overlap, priority_tie
    conflicting_schedule_id: uuid.UUID
    conflicting_schedule_name: str


class ActiveScheduleResponse(BaseModel):
    """Response for the active schedule endpoint."""

    active: bool
    schedule: UpcomingSchedule | None = None


# ============================================================================
# Helper Functions
# ============================================================================


def _parse_zone_ids(schedule: Schedule) -> list[uuid.UUID]:
    """Read zone_ids JSONB from a Schedule ORM instance and return as list[uuid.UUID]."""
    raw = schedule.zone_ids
    if not raw or not isinstance(raw, list):
        return []
    result: list[uuid.UUID] = []
    for item in raw:
        try:
            result.append(uuid.UUID(str(item)))
        except (ValueError, AttributeError):
            pass
    return result


async def _build_zone_map(db: AsyncSession, zone_uuids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Bulk-fetch zone names for a list of zone UUIDs."""
    if not zone_uuids:
        return {}
    result = await db.execute(select(Zone).where(Zone.id.in_(zone_uuids)))
    return {zone.id: zone.name for zone in result.scalars().all()}


async def _collect_all_zone_uuids(schedules: list[Schedule]) -> list[uuid.UUID]:
    """Collect all unique zone UUIDs across a list of schedules."""
    all_ids: set[uuid.UUID] = set()
    for s in schedules:
        all_ids.update(_parse_zone_ids(s))
    return list(all_ids)


def _build_schedule_response(
    schedule: Schedule,
    zone_map: dict[uuid.UUID, str],
    include_next: bool = True,
    tz: ZoneInfo | None = None,
) -> ScheduleResponse:
    """Build a ScheduleResponse from an ORM Schedule + zone name map.

    ``next_occurrence`` is computed in the user's timezone (``tz``) —
    previously UTC, which put it hours off and on the wrong weekday.
    """
    zone_uuids = _parse_zone_ids(schedule)
    zone_names = [zone_map[zid] for zid in zone_uuids if zid in zone_map]

    next_occurrence = None
    if include_next and schedule.is_enabled:
        next_occurrence = get_next_occurrence(
            schedule.days_of_week, schedule.start_time, tz=tz
        )

    return ScheduleResponse(
        id=schedule.id,
        name=schedule.name,
        zone_ids=zone_uuids,
        zone_names=zone_names,
        days_of_week=schedule.days_of_week,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        target_temp_c=schedule.target_temp_c,
        hvac_mode=schedule.hvac_mode,
        is_enabled=schedule.is_enabled,
        priority=schedule.priority,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
        next_occurrence=next_occurrence,
    )


async def _get_user_tz(db: AsyncSession) -> ZoneInfo:
    """Get the user's timezone — see ``backend.core.user_tz``."""
    from backend.api.dependencies import _ha_client
    from backend.core.user_tz import resolve_user_tz

    return await resolve_user_tz(db, _ha_client)


def parse_time(time_str: str) -> time:
    """Parse "HH:MM" (or "HH:MM:SS") to a time object; ValueError if invalid."""
    parts = str(time_str).strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid time {time_str!r}")
    return time(int(parts[0]), int(parts[1]))


def get_next_occurrence(
    days_of_week: list[int],
    start_time: str,
    from_time: datetime | None = None,
    tz: ZoneInfo | None = None,
) -> datetime:
    """Calculate the next occurrence of a schedule in the given timezone."""
    if tz is None:
        tz = ZoneInfo("UTC")

    # Work in local time
    if from_time is None:
        now_local = datetime.now(tz)
    else:
        now_local = from_time.astimezone(tz)

    target_time = parse_time(start_time)
    current_weekday = now_local.weekday()
    current_time = now_local.time()

    # Check each day starting from today
    for days_ahead in range(8):
        check_day = (current_weekday + days_ahead) % 7

        if check_day in days_of_week:
            # If it's today, check if the time hasn't passed
            if days_ahead == 0:
                if current_time < target_time:
                    local_dt = now_local.replace(
                        hour=target_time.hour,
                        minute=target_time.minute,
                        second=0,
                        microsecond=0,
                    )
                    return local_dt.astimezone(UTC)  # Return as UTC for consistency
            else:
                next_date = now_local + timedelta(days=days_ahead)
                local_dt = next_date.replace(
                    hour=target_time.hour,
                    minute=target_time.minute,
                    second=0,
                    microsecond=0,
                )
                return local_dt.astimezone(UTC)  # Return as UTC for consistency

    # Shouldn't happen if days_of_week is valid
    return from_time if from_time else datetime.now(UTC)


def check_schedule_overlap(
    schedule1: dict[str, Any],
    schedule2: dict[str, Any],
) -> bool:
    """Check if two schedules overlap in time and zones."""


    # Zones don't matter: there is one whole-house thermostat, so two
    # schedules active at the same time always compete — the higher
    # priority one silences the other entirely (spec §3.2).

    # Time overlap on the real weekly timeline, with the same rules the
    # control loops use (spec §3.1): each occurrence belongs to the day it
    # starts, the window is [start, end), end ≤ start wraps past midnight
    # (end == start = 24 h), no end = until midnight.  This also catches an
    # overnight part overlapping the *next* day's schedules.
    def _week_intervals(sched: dict[str, Any]) -> list[tuple[int, int]]:
        start_t = parse_time(sched.get("start_time", "00:00"))
        end_raw = sched.get("end_time")
        end_t = parse_time(end_raw) if end_raw else None
        out: list[tuple[int, int]] = []
        week = 7 * 1440
        for day in sched.get("days_of_week", []) or []:
            s_min = int(day) * 1440 + start_t.hour * 60 + start_t.minute
            if end_t is None:
                e_min = (int(day) + 1) * 1440
            else:
                e_min = int(day) * 1440 + end_t.hour * 60 + end_t.minute
                if end_t <= start_t:
                    e_min += 1440
            if e_min <= week:
                out.append((s_min, e_min))
            else:  # Sunday night wrapping into Monday
                out.append((s_min, week))
                out.append((0, e_min - week))
        return out

    return any(
        a0 < b1 and b0 < a1
        for a0, a1 in _week_intervals(schedule1)
        for b0, b1 in _week_intervals(schedule2)
    )


# ============================================================================
# Routes
# ============================================================================


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    db: Annotated[AsyncSession, Depends(get_db)],
    zone_id: Annotated[uuid.UUID | None, Query(description="Filter by zone")] = None,
    enabled_only: Annotated[bool, Query(description="Only return enabled schedules")] = False,
) -> list[ScheduleResponse]:
    """List all schedules with optional filtering."""
    stmt = select(Schedule).order_by(Schedule.priority.desc(), Schedule.name)

    if enabled_only:
        stmt = stmt.where(Schedule.is_enabled.is_(True))

    result = await db.execute(stmt)
    schedules = list(result.scalars().all())

    # If filtering by zone_id, filter in Python (JSONB contains check)
    if zone_id:
        zone_id_str = str(zone_id)
        schedules = [
            s for s in schedules
            if not s.zone_ids or zone_id_str in [str(zid) for zid in (s.zone_ids or [])]
        ]

    all_zone_uuids = await _collect_all_zone_uuids(schedules)
    zone_map = await _build_zone_map(db, all_zone_uuids)

    _tz = await _get_user_tz(db)
    return [_build_schedule_response(s, zone_map, tz=_tz) for s in schedules]


@router.get("/upcoming", response_model=list[UpcomingSchedule])
async def get_upcoming_schedules(
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: Annotated[int, Query(ge=1, le=168, description="Hours to look ahead")] = 24,
    zone_id: Annotated[uuid.UUID | None, Query(description="Filter by zone")] = None,
) -> list[UpcomingSchedule]:
    """
    Get upcoming schedule occurrences.

    Returns all scheduled events within the specified time window,
    sorted by start time.
    """
    now = datetime.now(UTC)
    end_window = now + timedelta(hours=hours)

    # Get enabled schedules
    stmt = select(Schedule).where(Schedule.is_enabled.is_(True))
    result = await db.execute(stmt)
    schedules = list(result.scalars().all())

    # Filter by zone if requested
    if zone_id:
        zone_id_str = str(zone_id)
        schedules = [
            s for s in schedules
            if not s.zone_ids or zone_id_str in [str(zid) for zid in (s.zone_ids or [])]
        ]

    all_zone_uuids = await _collect_all_zone_uuids(schedules)
    zone_map = await _build_zone_map(db, all_zone_uuids)

    user_tz = await _get_user_tz(db)

    upcoming: list[UpcomingSchedule] = []

    # Work entirely in local time to avoid UTC conversion confusion
    now_local = now.astimezone(user_tz)
    end_local = end_window.astimezone(user_tz)

    for schedule in schedules:
        zone_uuids = _parse_zone_ids(schedule)
        zone_names = [zone_map[zid] for zid in zone_uuids if zid in zone_map]

        start_t = parse_time(schedule.start_time)

        # Walk each day in the window and check if this schedule fires
        check_date = now_local.date()
        end_date = end_local.date()

        while check_date <= end_date:
            weekday = check_date.weekday()  # 0=Mon .. 6=Sun

            if weekday in (schedule.days_of_week or []):
                # Build the local start datetime for this day
                start_local = datetime.combine(
                    check_date, start_t, tzinfo=user_tz,
                )

                # Only include if it's in the future and within the window
                if start_local > now_local and start_local <= end_local:
                    start_utc = start_local.astimezone(UTC)

                    # Build end time in local, then convert to UTC
                    end_utc = None
                    if schedule.end_time:
                        end_t = parse_time(schedule.end_time)
                        end_local_dt = datetime.combine(
                            check_date, end_t, tzinfo=user_tz,
                        )
                        # If end < start, it wraps to the next day
                        if end_t <= start_t:
                            end_local_dt += timedelta(days=1)
                        end_utc = end_local_dt.astimezone(UTC)
                    else:
                        # No end time → until midnight (spec §3.1).
                        end_utc = datetime.combine(
                            check_date + timedelta(days=1), time(0, 0), tzinfo=user_tz,
                        ).astimezone(UTC)

                    upcoming.append(
                        UpcomingSchedule(
                            schedule_id=schedule.id,
                            schedule_name=schedule.name,
                            zone_ids=zone_uuids,
                            zone_names=zone_names,
                            start_time=start_utc,
                            end_time=end_utc,
                            target_temp_c=schedule.target_temp_c,
                            hvac_mode=schedule.hvac_mode,
                        )
                    )

            check_date += timedelta(days=1)

    # Sort by start time
    upcoming.sort(key=lambda x: x.start_time)

    return upcoming


@router.get("/conflicts", response_model=list[ScheduleConflict])
async def check_conflicts(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ScheduleConflict]:
    """
    Check for schedule conflicts.

    Returns a list of overlapping schedules that may cause
    unexpected behavior.
    """
    result = await db.execute(select(Schedule).where(Schedule.is_enabled.is_(True)))
    schedules = result.scalars().all()

    conflicts: list[ScheduleConflict] = []

    # Check each pair of schedules
    for i, s1 in enumerate(schedules):
        for s2 in schedules[i + 1 :]:
            s1_dict: dict[str, Any] = {
                "zone_ids": [str(zid) for zid in (s1.zone_ids or [])],
                "days_of_week": s1.days_of_week,
                "start_time": s1.start_time,
                "end_time": s1.end_time,
            }
            s2_dict: dict[str, Any] = {
                "zone_ids": [str(zid) for zid in (s2.zone_ids or [])],
                "days_of_week": s2.days_of_week,
                "start_time": s2.start_time,
                "end_time": s2.end_time,
            }

            if check_schedule_overlap(s1_dict, s2_dict):
                conflict_type = "overlap"
                if s1.priority == s2.priority:
                    conflict_type = "priority_tie"

                conflicts.append(
                    ScheduleConflict(
                        schedule_id=s1.id,
                        schedule_name=s1.name,
                        conflict_type=conflict_type,
                        conflicting_schedule_id=s2.id,
                        conflicting_schedule_name=s2.name,
                    )
                )

    return conflicts


@router.get("/active", response_model=ActiveScheduleResponse)
async def get_active_schedule(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ActiveScheduleResponse:
    """
    Get the currently active schedule, if any.

    A schedule is considered active when it is enabled, today's weekday
    is in its ``days_of_week``, and the current local time falls between
    its ``start_time`` and ``end_time``.  When ``end_time`` is ``None``
    the schedule is treated as active from ``start_time`` until 23:59.

    If multiple schedules are active simultaneously the one with the
    highest priority is returned.
    """
    user_tz = await _get_user_tz(db)
    now_local = datetime.now(user_tz)
    now_local.weekday()  # 0=Mon .. 6=Sun
    now_local.time()

    # Fetch all enabled schedules
    stmt = select(Schedule).where(Schedule.is_enabled.is_(True))
    result = await db.execute(stmt)
    schedules = list(result.scalars().all())

    from backend.core.schedule_guard import active_occurrence, find_active_schedule

    best = find_active_schedule(schedules, now_local)
    if best is None:
        return ActiveScheduleResponse(active=False, schedule=None)
    occurrence = active_occurrence(best.days_of_week, best.start_time, best.end_time, now_local)

    zone_uuids = _parse_zone_ids(best)
    zone_map = await _build_zone_map(db, zone_uuids)
    zone_names = [zone_map[zid] for zid in zone_uuids if zid in zone_map]

    # Start/end of the occurrence actually in progress (an overnight window
    # seen after midnight started *yesterday*), in UTC.
    if occurrence is None:  # pragma: no cover — find_active_schedule guarantees one
        return ActiveScheduleResponse(active=False, schedule=None)
    start_dt = occurrence[0].astimezone(UTC)
    end_dt = occurrence[1].astimezone(UTC)  # no end time → midnight (spec §3.1)

    return ActiveScheduleResponse(
        active=True,
        schedule=UpcomingSchedule(
            schedule_id=best.id,
            schedule_name=best.name,
            zone_ids=zone_uuids,
            zone_names=zone_names,
            start_time=start_dt,
            end_time=end_dt,
            target_temp_c=best.target_temp_c,
            hvac_mode=best.hvac_mode,
        ),
    )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduleResponse:
    """Get a specific schedule by ID."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map, tz=await _get_user_tz(db))


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduleResponse:
    """Create a new schedule."""
    # Validate all zone_ids exist
    if payload.zone_ids:
        zone_result = await db.execute(select(Zone).where(Zone.id.in_(payload.zone_ids)))
        found_zones = {z.id for z in zone_result.scalars().all()}
        missing = set(payload.zone_ids) - found_zones
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Zone(s) not found: {', '.join(str(z) for z in missing)}",
            )

    zone_ids_json = [str(zid) for zid in payload.zone_ids]

    schedule = Schedule(
        name=payload.name,
        zone_ids=zone_ids_json,
        days_of_week=payload.days_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        target_temp_c=payload.target_temp_c,
        hvac_mode=payload.hvac_mode,
        is_enabled=payload.is_enabled,
        priority=payload.priority,
    )

    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    # Apply immediately if the schedule is currently active
    if schedule.is_enabled:
        _fire_apply_now(schedule)

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map, tz=await _get_user_tz(db))


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduleResponse:
    """Update an existing schedule."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )

    # Validate zone_ids if being changed
    if payload.zone_ids is not None and payload.zone_ids:
        zone_result = await db.execute(select(Zone).where(Zone.id.in_(payload.zone_ids)))
        found_zones = {z.id for z in zone_result.scalars().all()}
        missing = set(payload.zone_ids) - found_zones
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Zone(s) not found: {', '.join(str(z) for z in missing)}",
            )

    # Update fields
    # Explicit nulls are only meaningful for end_time ("no end") — on the
    # NOT NULL columns they raised an IntegrityError 500.
    update_data = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None or k in ("end_time", "zone_ids")
    }

    # Handle zone_ids specially — convert UUIDs to strings for JSONB storage
    if "zone_ids" in update_data:
        raw_zone_ids = update_data.pop("zone_ids")
        if raw_zone_ids is not None:
            schedule.zone_ids = [str(zid) for zid in raw_zone_ids]
        else:
            schedule.zone_ids = []

    for key, value in update_data.items():
        setattr(schedule, key, value)

    schedule.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(schedule)

    # Apply immediately if the updated schedule is currently active
    if schedule.is_enabled:
        _fire_apply_now(schedule)

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map, tz=await _get_user_tz(db))


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a schedule."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )

    await db.delete(schedule)
    await db.commit()


@router.post("/{schedule_id}/enable", response_model=ScheduleResponse)
async def enable_schedule(
    schedule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduleResponse:
    """Enable a schedule."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )

    schedule.is_enabled = True
    schedule.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(schedule)

    # Apply immediately if the now-enabled schedule is currently active
    _fire_apply_now(schedule)

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map, tz=await _get_user_tz(db))


@router.post("/{schedule_id}/disable", response_model=ScheduleResponse)
async def disable_schedule(
    schedule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduleResponse:
    """Disable a schedule."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )

    schedule.is_enabled = False
    schedule.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(schedule)

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map, include_next=False)
