"""Schedule API routes for ClimateIQ."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.models.database import Schedule, Zone

router = APIRouter()


# ============================================================================
# Pydantic Models
# ============================================================================


class ScheduleCreate(BaseModel):
    """Schedule creation request."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=100)
    zone_ids: list[uuid.UUID] = Field(default_factory=list)  # empty = all zones
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon, 6=Sun
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")  # HH:MM format
    end_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    target_temp_c: float = Field(..., ge=4.4, le=37.8)
    hvac_mode: str = Field(default="auto")  # auto, heating, cooling, off
    is_enabled: bool = True
    priority: int = Field(default=1, ge=1, le=10)  # Higher = more important

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("At least one day must be selected")
        for day in v:
            if day < 0 or day > 6:
                raise ValueError("Days must be between 0 (Monday) and 6 (Sunday)")
        return sorted(set(v))

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

    name: str | None = None
    zone_ids: list[uuid.UUID] | None = None
    days_of_week: list[int] | None = None
    start_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    target_temp_c: float | None = Field(None, ge=4.4, le=37.8)
    hvac_mode: str | None = None
    is_enabled: bool | None = None
    priority: int | None = Field(None, ge=1, le=10)


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
) -> ScheduleResponse:
    """Build a ScheduleResponse from an ORM Schedule + zone name map."""
    zone_uuids = _parse_zone_ids(schedule)
    zone_names = [zone_map[zid] for zid in zone_uuids if zid in zone_map]

    next_occurrence = None
    if include_next and schedule.is_enabled:
        next_occurrence = get_next_occurrence(schedule.days_of_week, schedule.start_time)

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


def parse_time(time_str: str) -> time:
    """Parse HH:MM string to time object."""
    hours, minutes = map(int, time_str.split(":"))
    return time(hours, minutes)


def get_next_occurrence(
    days_of_week: list[int],
    start_time: str,
    from_time: datetime | None = None,
) -> datetime:
    """Calculate the next occurrence of a schedule."""
    if from_time is None:
        from_time = datetime.now(UTC)

    target_time = parse_time(start_time)
    current_weekday = from_time.weekday()
    current_time = from_time.time()

    # Check each day starting from today
    for days_ahead in range(8):
        check_day = (current_weekday + days_ahead) % 7

        if check_day in days_of_week:
            # If it's today, check if the time hasn't passed
            if days_ahead == 0:
                if current_time < target_time:
                    return from_time.replace(
                        hour=target_time.hour,
                        minute=target_time.minute,
                        second=0,
                        microsecond=0,
                    )
            else:
                next_date = from_time + timedelta(days=days_ahead)
                return next_date.replace(
                    hour=target_time.hour,
                    minute=target_time.minute,
                    second=0,
                    microsecond=0,
                )

    # Shouldn't happen if days_of_week is valid
    return from_time


def check_schedule_overlap(
    schedule1: dict[str, Any],
    schedule2: dict[str, Any],
) -> bool:
    """Check if two schedules overlap in time and zones."""
    # Check if they share any days
    days1 = set(schedule1.get("days_of_week", []))
    days2 = set(schedule2.get("days_of_week", []))

    if not days1 & days2:
        return False

    # Check if they share zones (empty/None = all zones, always overlaps)
    # Support both new `zone_ids` (list) and legacy `zone_id` (single value)
    zone_ids1: list[str] = schedule1.get("zone_ids", [])
    zone_ids2: list[str] = schedule2.get("zone_ids", [])

    # Fallback to legacy zone_id if zone_ids is empty
    if not zone_ids1 and schedule1.get("zone_id") is not None:
        zone_ids1 = [str(schedule1["zone_id"])]
    if not zone_ids2 and schedule2.get("zone_id") is not None:
        zone_ids2 = [str(schedule2["zone_id"])]

    # If either targets all zones (empty list), they overlap on zones
    if zone_ids1 and zone_ids2:
        # Both have specific zones — check for intersection
        if not set(zone_ids1) & set(zone_ids2):
            return False

    # Check time overlap
    start1 = parse_time(schedule1.get("start_time", "00:00"))
    end1 = (
        parse_time(schedule1.get("end_time", "23:59"))
        if schedule1.get("end_time")
        else time(23, 59)
    )
    start2 = parse_time(schedule2.get("start_time", "00:00"))
    end2 = (
        parse_time(schedule2.get("end_time", "23:59"))
        if schedule2.get("end_time")
        else time(23, 59)
    )

    # Check for overlap
    return start1 < end2 and start2 < end1


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

    return [_build_schedule_response(s, zone_map) for s in schedules]


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

    upcoming: list[UpcomingSchedule] = []

    for schedule in schedules:
        zone_uuids = _parse_zone_ids(schedule)
        zone_names = [zone_map[zid] for zid in zone_uuids if zid in zone_map]

        # Calculate occurrences within the window
        current_check = now

        while current_check < end_window:
            next_start = get_next_occurrence(
                schedule.days_of_week,
                schedule.start_time,
                current_check,
            )

            if next_start >= end_window:
                break

            # Calculate end time
            end_dt = None
            if schedule.end_time:
                end_t = parse_time(schedule.end_time)
                end_dt = next_start.replace(hour=end_t.hour, minute=end_t.minute)
                if end_dt <= next_start:
                    end_dt += timedelta(days=1)

            upcoming.append(
                UpcomingSchedule(
                    schedule_id=schedule.id,
                    schedule_name=schedule.name,
                    zone_ids=zone_uuids,
                    zone_names=zone_names,
                    start_time=next_start,
                    end_time=end_dt,
                    target_temp_c=schedule.target_temp_c,
                    hvac_mode=schedule.hvac_mode,
                )
            )

            # Move to next day to find next occurrence
            current_check = next_start + timedelta(days=1)

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

    return _build_schedule_response(schedule, zone_map)


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

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map)


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
    update_data = payload.model_dump(exclude_unset=True)

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

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map)


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

    zone_uuids = _parse_zone_ids(schedule)
    zone_map = await _build_zone_map(db, zone_uuids)

    return _build_schedule_response(schedule, zone_map)


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
