"""Analytics API routes for ClimateIQ.

Provides temperature/humidity history, occupancy patterns, energy usage
estimates, and comfort scoring per zone.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db, get_ha_client
from backend.integrations import HAClient
from backend.integrations.ha_client import HAClientError
from backend.models.database import (
    Device,
    DeviceAction,
    OccupancyPattern,
    Sensor,
    SensorReading,
    SystemSetting,
    Zone,
)
from backend.models.enums import DeviceType, PatternType, Season
from backend.models.schemas import OccupancyPatternResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class ReadingPoint(BaseModel):
    """A single data point in a time series."""

    recorded_at: datetime
    temperature_c: float | None = None
    humidity: float | None = None
    presence: bool | None = None
    lux: float | None = None
    sensor_id: uuid.UUID


class HistoryResponse(BaseModel):
    """Temperature/humidity history for a zone."""

    zone_id: uuid.UUID
    zone_name: str
    period_start: datetime
    period_end: datetime
    total_readings: int
    avg_temperature_c: float | None = None
    min_temperature_c: float | None = None
    max_temperature_c: float | None = None
    avg_humidity: float | None = None
    min_humidity: float | None = None
    max_humidity: float | None = None
    readings: list[ReadingPoint] = Field(default_factory=list)


class EnergyZoneEstimate(BaseModel):
    """Energy usage estimate for a single zone."""

    zone_id: uuid.UUID
    zone_name: str
    device_count: int
    action_count: int
    estimated_kwh: float
    estimated_cost_usd: float
    primary_device_type: str | None = None


class EnergyResponse(BaseModel):
    """Aggregate energy usage estimates."""

    period_start: datetime
    period_end: datetime
    total_estimated_kwh: float
    total_estimated_cost_usd: float
    cost_per_kwh: float
    zones: list[EnergyZoneEstimate] = Field(default_factory=list)
    estimation_note: str = Field(
        default=(
            "Energy values are heuristic estimates based on device action counts "
            "and average wattage ratings. Actual consumption may vary. For precise "
            "metering, integrate a dedicated energy monitor."
        ),
        description="Explanation of how energy estimates are calculated",
    )


class ComfortZoneScore(BaseModel):
    """Comfort score for a single zone."""

    zone_id: uuid.UUID
    zone_name: str
    score: float = Field(description="0-100 comfort score")
    avg_temperature_c: float | None = None
    avg_humidity: float | None = None
    temp_in_range_pct: float = Field(description="Percentage of readings in comfort range")
    humidity_in_range_pct: float = Field(description="Percentage of readings in comfort range")
    reading_count: int = 0
    factors: dict[str, Any] = Field(default_factory=dict)


class ComfortResponse(BaseModel):
    """Comfort scores across all zones."""

    period_start: datetime
    period_end: datetime
    overall_score: float
    zones: list[ComfortZoneScore] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Comfort range defaults
# ---------------------------------------------------------------------------
COMFORT_TEMP_MIN = 20.0  # °C
COMFORT_TEMP_MAX = 24.0  # °C
COMFORT_HUMIDITY_MIN = 30.0  # %
COMFORT_HUMIDITY_MAX = 60.0  # %

# Rough energy estimates per device action (kWh per action-hour)
DEVICE_ENERGY_RATES: dict[DeviceType, float] = {
    DeviceType.thermostat: 3.0,
    DeviceType.mini_split: 1.5,
    DeviceType.space_heater: 1.5,
    DeviceType.fan: 0.075,
    DeviceType.humidifier: 0.3,
    DeviceType.dehumidifier: 0.5,
    DeviceType.smart_vent: 0.01,
    DeviceType.blind: 0.005,
    DeviceType.shade: 0.005,
    DeviceType.other: 0.1,
}


# ---------------------------------------------------------------------------
# GET /analytics/zones/{zone_id}/history
# ---------------------------------------------------------------------------
@router.get("/zones/{zone_id}/history", response_model=HistoryResponse)
async def get_zone_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    zone_id: uuid.UUID,
    hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 24,
    resolution: Annotated[
        int,
        Query(
            ge=0,
            le=3600,
            description="Bucket size in seconds for downsampling. 0 = raw readings.",
        ),
    ] = 0,
) -> HistoryResponse:
    """Return temperature and humidity history for a zone.

    Supports an optional `resolution` parameter to downsample readings into
    fixed-width time buckets (e.g. 300 = 5-minute averages).
    """
    zone = await _require_zone(db, zone_id)

    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(hours=hours)

    # Get sensor IDs for this zone
    sensor_ids = await _zone_sensor_ids(db, zone_id)
    if not sensor_ids:
        return HistoryResponse(
            zone_id=zone_id,
            zone_name=zone.name,
            period_start=period_start,
            period_end=period_end,
            total_readings=0,
        )

    # Fetch raw readings
    stmt = (
        select(SensorReading)
        .where(
            SensorReading.sensor_id.in_(sensor_ids),
            SensorReading.recorded_at >= period_start,
            SensorReading.recorded_at <= period_end,
        )
        .order_by(SensorReading.recorded_at.asc())
    )
    result = await db.execute(stmt)
    raw_readings = list(result.scalars().all())

    # Compute aggregates
    temps = [r.temperature_c for r in raw_readings if r.temperature_c is not None]
    humids = [r.humidity for r in raw_readings if r.humidity is not None]

    # Build reading points (optionally downsampled)
    points: list[ReadingPoint] = []
    if resolution > 0 and raw_readings:
        points = _downsample(raw_readings, resolution)
    else:
        points = [
            ReadingPoint(
                recorded_at=r.recorded_at,
                temperature_c=r.temperature_c,
                humidity=r.humidity,
                presence=r.presence,
                lux=r.lux,
                sensor_id=r.sensor_id,
            )
            for r in raw_readings
        ]

    return HistoryResponse(
        zone_id=zone_id,
        zone_name=zone.name,
        period_start=period_start,
        period_end=period_end,
        total_readings=len(raw_readings),
        avg_temperature_c=round(sum(temps) / len(temps), 2) if temps else None,
        min_temperature_c=round(min(temps), 2) if temps else None,
        max_temperature_c=round(max(temps), 2) if temps else None,
        avg_humidity=round(sum(humids) / len(humids), 2) if humids else None,
        min_humidity=round(min(humids), 2) if humids else None,
        max_humidity=round(max(humids), 2) if humids else None,
        readings=points,
    )


# ---------------------------------------------------------------------------
# GET /analytics/zones/{zone_id}/patterns
# ---------------------------------------------------------------------------
@router.get("/zones/{zone_id}/patterns", response_model=list[OccupancyPatternResponse])
async def get_zone_patterns(
    db: Annotated[AsyncSession, Depends(get_db)],
    zone_id: uuid.UUID,
    pattern_type: Annotated[
        PatternType | None,
        Query(description="Filter by pattern type"),
    ] = None,
    season: Annotated[Season | None, Query(description="Filter by season")] = None,
) -> list[OccupancyPatternResponse]:
    """Return learned occupancy patterns for a zone."""
    await _require_zone(db, zone_id)

    stmt = (
        select(OccupancyPattern)
        .where(OccupancyPattern.zone_id == zone_id)
        .order_by(OccupancyPattern.created_at.desc())
    )
    if pattern_type is not None:
        stmt = stmt.where(OccupancyPattern.pattern_type == pattern_type)
    if season is not None:
        stmt = stmt.where(OccupancyPattern.season == season)

    result = await db.execute(stmt)
    patterns = result.scalars().all()
    return [OccupancyPatternResponse.model_validate(p) for p in patterns]


# ---------------------------------------------------------------------------
# GET /analytics/energy
# ---------------------------------------------------------------------------
@router.get("/energy", response_model=EnergyResponse)
async def get_energy_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 24,
    cost_per_kwh: Annotated[
        float,
        Query(ge=0.0, description="Electricity cost per kWh in USD"),
    ] = 0.12,
) -> EnergyResponse:
    """Estimate energy usage across all zones based on device actions.

    This is a heuristic estimate: each device action is assumed to represent
    a period of active operation, and energy is estimated using rough per-device
    wattage rates.
    """
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(hours=hours)

    # Fetch all zones with devices
    zones_result = await db.execute(select(Zone).order_by(Zone.name))
    zones = zones_result.scalars().all()
    if not zones:
        return EnergyResponse(
            period_start=period_start,
            period_end=period_end,
            total_estimated_kwh=0.0,
            total_estimated_cost_usd=0.0,
            cost_per_kwh=cost_per_kwh,
            zones=[],
        )

    zone_ids = [zone.id for zone in zones]
    devices_result = await db.execute(select(Device).where(Device.zone_id.in_(zone_ids)))
    devices = devices_result.scalars().all()
    devices_by_zone: dict[uuid.UUID, list[Device]] = {}
    for device in devices:
        devices_by_zone.setdefault(device.zone_id, []).append(device)

    device_ids = [device.id for device in devices]
    if not device_ids:
        return EnergyResponse(
            period_start=period_start,
            period_end=period_end,
            total_estimated_kwh=0.0,
            total_estimated_cost_usd=0.0,
            cost_per_kwh=cost_per_kwh,
            zones=[],
        )

    action_counts_result = await db.execute(
        select(DeviceAction.device_id, func.count(DeviceAction.id))
        .where(
            DeviceAction.device_id.in_(device_ids),
            DeviceAction.created_at >= period_start,
            DeviceAction.created_at <= period_end,
        )
        .group_by(DeviceAction.device_id)
    )
    action_counts: dict[uuid.UUID, int] = {
        row[0]: int(row[1]) for row in action_counts_result.all() if row[0]
    }

    primary_types_result = await db.execute(
        select(Device.zone_id, Device.type, func.count(DeviceAction.id).label("cnt"))
        .join(DeviceAction, DeviceAction.device_id == Device.id)
        .where(
            Device.zone_id.in_(zone_ids),
            DeviceAction.created_at >= period_start,
            DeviceAction.created_at <= period_end,
        )
        .group_by(Device.zone_id, Device.type)
    )
    primary_type_counts: dict[uuid.UUID, dict[DeviceType, int]] = {}
    for zone_id, device_type, count in primary_types_result.all():
        if zone_id is None or device_type is None:
            continue
        primary_type_counts.setdefault(zone_id, {})[device_type] = int(count)

    zone_estimates: list[EnergyZoneEstimate] = []
    total_kwh = 0.0

    for zone in zones:
        devices = devices_by_zone.get(zone.id, [])
        if not devices:
            continue

        device_ids = [d.id for d in devices]
        action_count = sum(action_counts.get(device_id, 0) for device_id in device_ids)

        if action_count == 0:
            continue

        # Determine primary device type (most actions)
        type_counts = primary_type_counts.get(zone.id, {})
        primary_device_type = None
        if type_counts:
            primary_device_type = max(type_counts.items(), key=lambda item: item[1])[0].value

        # Estimate energy: actions * average_rate * assumed_duration_hours
        # Assume each action represents ~15 minutes of operation
        avg_rate = sum(DEVICE_ENERGY_RATES.get(d.type, 0.1) for d in devices) / len(devices)
        estimated_kwh = round(action_count * avg_rate * 0.25, 3)  # 15 min = 0.25 hr
        total_kwh += estimated_kwh

        zone_estimates.append(
            EnergyZoneEstimate(
                zone_id=zone.id,
                zone_name=zone.name,
                device_count=len(devices),
                action_count=action_count,
                estimated_kwh=estimated_kwh,
                estimated_cost_usd=round(estimated_kwh * cost_per_kwh, 4),
                primary_device_type=primary_device_type,
            )
        )

    return EnergyResponse(
        period_start=period_start,
        period_end=period_end,
        total_estimated_kwh=round(total_kwh, 3),
        total_estimated_cost_usd=round(total_kwh * cost_per_kwh, 4),
        cost_per_kwh=cost_per_kwh,
        zones=zone_estimates,
    )


# ---------------------------------------------------------------------------
# GET /analytics/comfort
# ---------------------------------------------------------------------------
@router.get("/comfort", response_model=ComfortResponse)
async def get_comfort_scores(
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 24,
    temp_min: Annotated[
        float,
        Query(description="Comfort temp lower bound (°C)"),
    ] = COMFORT_TEMP_MIN,
    temp_max: Annotated[
        float,
        Query(description="Comfort temp upper bound (°C)"),
    ] = COMFORT_TEMP_MAX,
    humidity_min: Annotated[
        float,
        Query(description="Comfort humidity lower bound (%)"),
    ] = COMFORT_HUMIDITY_MIN,
    humidity_max: Annotated[
        float,
        Query(description="Comfort humidity upper bound (%)"),
    ] = COMFORT_HUMIDITY_MAX,
) -> ComfortResponse:
    """Compute a 0-100 comfort score for each zone based on recent readings.

    The score is a weighted combination of:
    - Temperature in-range percentage (60% weight)
    - Humidity in-range percentage (40% weight)
    """
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(hours=hours)

    zones_result = await db.execute(
        select(Zone).where(Zone.is_active.is_(True)).order_by(Zone.name)
    )
    zones = zones_result.scalars().all()

    zone_scores: list[ComfortZoneScore] = []

    for zone in zones:
        sensor_ids = await _zone_sensor_ids(db, zone.id)
        if not sensor_ids:
            zone_scores.append(
                ComfortZoneScore(
                    zone_id=zone.id,
                    zone_name=zone.name,
                    score=0.0,
                    temp_in_range_pct=0.0,
                    humidity_in_range_pct=0.0,
                    reading_count=0,
                    factors={"note": "No sensors in zone"},
                )
            )
            continue

        # Fetch readings in period
        stmt = select(SensorReading).where(
            SensorReading.sensor_id.in_(sensor_ids),
            SensorReading.recorded_at >= period_start,
            SensorReading.recorded_at <= period_end,
        )
        result = await db.execute(stmt)
        readings = result.scalars().all()

        if not readings:
            zone_scores.append(
                ComfortZoneScore(
                    zone_id=zone.id,
                    zone_name=zone.name,
                    score=0.0,
                    temp_in_range_pct=0.0,
                    humidity_in_range_pct=0.0,
                    reading_count=0,
                    factors={"note": "No readings in period"},
                )
            )
            continue

        temps = [r.temperature_c for r in readings if r.temperature_c is not None]
        humids = [r.humidity for r in readings if r.humidity is not None]

        temp_in_range = sum(1 for t in temps if temp_min <= t <= temp_max)
        temp_pct = (temp_in_range / len(temps) * 100.0) if temps else 0.0

        humid_in_range = sum(1 for h in humids if humidity_min <= h <= humidity_max)
        humid_pct = (humid_in_range / len(humids) * 100.0) if humids else 0.0

        # Weighted score: 60% temperature, 40% humidity
        # If one metric has no data, the other gets full weight
        if temps and humids:
            score = 0.6 * temp_pct + 0.4 * humid_pct
        elif temps:
            score = temp_pct
        elif humids:
            score = humid_pct
        else:
            score = 0.0

        avg_temp = round(sum(temps) / len(temps), 2) if temps else None
        avg_humid = round(sum(humids) / len(humids), 2) if humids else None

        zone_scores.append(
            ComfortZoneScore(
                zone_id=zone.id,
                zone_name=zone.name,
                score=round(score, 1),
                avg_temperature_c=avg_temp,
                avg_humidity=avg_humid,
                temp_in_range_pct=round(temp_pct, 1),
                humidity_in_range_pct=round(humid_pct, 1),
                reading_count=len(readings),
                factors={
                    "temp_readings": len(temps),
                    "humidity_readings": len(humids),
                    "comfort_range": {
                        "temp_min": temp_min,
                        "temp_max": temp_max,
                        "humidity_min": humidity_min,
                        "humidity_max": humidity_max,
                    },
                },
            )
        )

    # Overall score: average of zone scores (only zones with readings)
    scored_zones = [z for z in zone_scores if z.reading_count > 0]
    overall = (
        round(sum(z.score for z in scored_zones) / len(scored_zones), 1) if scored_zones else 0.0
    )

    return ComfortResponse(
        period_start=period_start,
        period_end=period_end,
        overall_score=overall,
        zones=zone_scores,
    )


# ---------------------------------------------------------------------------
# Response schema for decisions
# ---------------------------------------------------------------------------
class DecisionRecord(BaseModel):
    """A single device action / decision record."""

    id: uuid.UUID
    device_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    zone_name: str | None = None
    device_name: str | None = None
    triggered_by: str
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    reasoning: str | None = None
    mode: str | None = None
    created_at: datetime


class DecisionsResponse(BaseModel):
    """Paginated decision history."""

    period_start: datetime
    period_end: datetime
    total_count: int
    decisions: list[DecisionRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /analytics/decisions
# ---------------------------------------------------------------------------
@router.get("/decisions", response_model=DecisionsResponse)
async def get_decisions(
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 24,
    zone_id: Annotated[uuid.UUID | None, Query(description="Filter by zone")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Max results")] = 100,
    offset: Annotated[int, Query(ge=0, description="Result offset")] = 0,
) -> DecisionsResponse:
    """Return device action / decision history.

    Provides the complete decision log including the LLM reasoning, action
    parameters, and results so users can audit what the system has done.
    """
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(hours=hours)

    # Base filter
    conditions = [
        DeviceAction.created_at >= period_start,
        DeviceAction.created_at <= period_end,
    ]
    if zone_id is not None:
        conditions.append(DeviceAction.zone_id == zone_id)

    # Total count
    count_stmt = select(func.count(DeviceAction.id)).where(*conditions)
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0

    # Fetch actions with device + zone info
    stmt = (
        select(DeviceAction, Device.name.label("device_name"), Zone.name.label("zone_name"))
        .join(Device, DeviceAction.device_id == Device.id)
        .outerjoin(Zone, DeviceAction.zone_id == Zone.id)
        .where(*conditions)
        .order_by(DeviceAction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.all()

    decisions = [
        DecisionRecord(
            id=action.id,
            device_id=action.device_id,
            zone_id=action.zone_id,
            zone_name=z_name,
            device_name=d_name,
            triggered_by=action.triggered_by.value if action.triggered_by else "unknown",
            action_type=action.action_type.value if action.action_type else "unknown",
            parameters=action.parameters or {},
            result=action.result,
            reasoning=action.reasoning,
            mode=action.mode.value if action.mode else None,
            created_at=action.created_at,
        )
        for action, d_name, z_name in rows
    ]

    return DecisionsResponse(
        period_start=period_start,
        period_end=period_end,
        total_count=total_count,
        decisions=decisions,
    )


# ---------------------------------------------------------------------------
# Response schema for energy/live
# ---------------------------------------------------------------------------
class EnergyLiveResponse(BaseModel):
    """Live energy reading from a Home Assistant entity."""

    configured: bool
    value: float | None = None
    unit: str | None = None
    entity_id: str | None = None
    friendly_name: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# GET /analytics/energy/live
# ---------------------------------------------------------------------------
@router.get("/energy/live", response_model=EnergyLiveResponse)
async def get_energy_live(
    db: Annotated[AsyncSession, Depends(get_db)],
    ha_client: Annotated[HAClient, Depends(get_ha_client)],
) -> EnergyLiveResponse:
    """Return the current live energy reading from a configured HA entity.

    Reads the ``energy_entity`` setting from the database. If not configured,
    returns ``configured=false``. Otherwise fetches the entity state from HA.
    """
    energy_entity = await _get_energy_entity(db)
    if not energy_entity:
        return EnergyLiveResponse(configured=False)

    try:
        state = await ha_client.get_state(energy_entity)
        return EnergyLiveResponse(
            configured=True,
            value=float(state.state),
            unit=state.attributes.get("unit_of_measurement", "kWh"),
            entity_id=energy_entity,
            friendly_name=state.attributes.get("friendly_name", ""),
        )
    except (HAClientError, ValueError, Exception) as exc:
        logger.warning("Failed to fetch energy entity %s: %s", energy_entity, exc)
        return EnergyLiveResponse(
            configured=True,
            value=None,
            unit=None,
            entity_id=energy_entity,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_energy_entity(db: AsyncSession) -> str:
    """Read the configured energy entity from the key-value table.

    Returns an empty string if not configured.
    """
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "energy_entity"))
    row = result.scalar_one_or_none()
    if row is None:
        return ""
    value: str = row.value.get("value", "")
    return value


async def _require_zone(db: AsyncSession, zone_id: uuid.UUID) -> Zone:
    """Load a zone or raise 404."""
    result = await db.execute(select(Zone).where(Zone.id == zone_id))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone {zone_id} not found",
        )
    return zone


async def _zone_sensor_ids(db: AsyncSession, zone_id: uuid.UUID) -> list[uuid.UUID]:
    """Return sensor IDs belonging to a zone."""
    result = await db.execute(select(Sensor.id).where(Sensor.zone_id == zone_id))
    return [row[0] for row in result.all()]


def _downsample(
    readings: list[SensorReading],
    bucket_seconds: int,
) -> list[ReadingPoint]:
    """Downsample readings into fixed-width time buckets using averages."""
    if not readings:
        return []

    buckets: dict[int, list[Any]] = {}
    for r in readings:
        ts = r.recorded_at.timestamp()
        bucket_key = int(ts // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_key, []).append(r)

    points: list[ReadingPoint] = []
    for bucket_ts in sorted(buckets.keys()):
        group = buckets[bucket_ts]
        temps = [r.temperature_c for r in group if r.temperature_c is not None]
        humids = [r.humidity for r in group if r.humidity is not None]
        luxes = [r.lux for r in group if r.lux is not None]
        presences = [r.presence for r in group if r.presence is not None]

        points.append(
            ReadingPoint(
                recorded_at=datetime.fromtimestamp(bucket_ts, tz=UTC),
                temperature_c=round(sum(temps) / len(temps), 2) if temps else None,
                humidity=round(sum(humids) / len(humids), 2) if humids else None,
                presence=any(presences) if presences else None,
                lux=round(sum(luxes) / len(luxes), 2) if luxes else None,
                sensor_id=group[0].sensor_id,  # representative sensor
            )
        )

    return points


__all__ = ["router"]
