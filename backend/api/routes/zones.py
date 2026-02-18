"""Zone CRUD and readings API routes for ClimateIQ."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.dependencies import get_db, get_ha_client
from backend.integrations import HAClient
from backend.integrations.ha_client import HAClientError
from backend.models.database import Sensor, SensorReading, Zone
from backend.models.schemas import SensorReadingResponse, ZoneCreate, ZoneResponse, ZoneUpdate

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# HA unit detection — cached so we only query HA config once
# ---------------------------------------------------------------------------
_ha_temp_unit: str | None = None  # "°F", "°C", or None (unknown)


def _f_to_c(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32.0) * 5.0 / 9.0


async def _get_ha_temp_unit(ha_client: HAClient) -> str:
    """Detect HA's configured temperature unit (cached after first call)."""
    global _ha_temp_unit
    if _ha_temp_unit is not None:
        return _ha_temp_unit
    try:
        config = await ha_client.get_config()
        # HA config returns unit_system.temperature = "°F" or "°C"
        unit_system = config.get("unit_system", {})
        _ha_temp_unit = unit_system.get("temperature", "°C")
        logger.info("Detected HA temperature unit: %s", _ha_temp_unit)
    except Exception as exc:
        logger.warning("Could not detect HA temp unit, assuming °C: %s", exc)
        _ha_temp_unit = "°C"
    return _ha_temp_unit


def _ha_temp_to_celsius(value: float, ha_unit: str) -> float:
    """Convert an HA temperature value to Celsius if needed."""
    if ha_unit == "°F":
        return _f_to_c(value)
    return value


# ---------------------------------------------------------------------------
# GET /zones — list all zones with related sensors and devices
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ZoneResponse])
async def list_zones(
    db: Annotated[AsyncSession, Depends(get_db)],
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
    floor: Annotated[int | None, Query(description="Filter by floor number")] = None,
) -> list[ZoneResponse]:
    """Return every zone with its sensors and devices eagerly loaded."""
    stmt = (
        select(Zone)
        .options(selectinload(Zone.sensors), selectinload(Zone.devices))
        .order_by(Zone.name)
    )
    if is_active is not None:
        stmt = stmt.where(Zone.is_active == is_active)
    if floor is not None:
        stmt = stmt.where(Zone.floor == floor)

    # Try to get HA client for live thermostat data (non-fatal if unavailable)
    ha_client: HAClient | None = None
    try:
        from backend.api.dependencies import _ha_client
        ha_client = _ha_client
    except Exception:
        pass

    result = await db.execute(stmt)
    zones = result.scalars().unique().all()
    return [await _enrich_zone_response(db, z, ha_client) for z in zones]


# ---------------------------------------------------------------------------
# GET /zones/{zone_id} — single zone detail
# ---------------------------------------------------------------------------
@router.get("/{zone_id}", response_model=ZoneResponse)
async def get_zone(
    zone_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ZoneResponse:
    """Return a single zone by ID with sensors and devices."""
    # Try to get HA client for live thermostat data (non-fatal if unavailable)
    ha_client: HAClient | None = None
    try:
        from backend.api.dependencies import _ha_client
        ha_client = _ha_client
    except Exception:
        pass

    zone = await _fetch_zone(db, zone_id)
    return await _enrich_zone_response(db, zone, ha_client)


# ---------------------------------------------------------------------------
# POST /zones — create a new zone
# ---------------------------------------------------------------------------
@router.post("", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
async def create_zone(
    payload: ZoneCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ZoneResponse:
    """Create a new zone."""
    zone = Zone(**payload.model_dump())
    db.add(zone)
    await db.commit()
    await db.refresh(zone, attribute_names=["sensors", "devices"])
    return ZoneResponse.model_validate(zone)


# ---------------------------------------------------------------------------
# PUT /zones/{zone_id} — update an existing zone
# ---------------------------------------------------------------------------
@router.put("/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: uuid.UUID,
    payload: ZoneUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ZoneResponse:
    """Partially update a zone. Only supplied fields are changed."""
    zone = await _fetch_zone(db, zone_id)

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update",
        )

    for key, value in update_data.items():
        setattr(zone, key, value)

    zone.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(zone, attribute_names=["sensors", "devices"])
    return ZoneResponse.model_validate(zone)


# ---------------------------------------------------------------------------
# DELETE /zones/{zone_id} — delete a zone (cascades sensors/devices)
# ---------------------------------------------------------------------------
@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    zone_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a zone and all associated sensors/devices via cascade."""
    zone = await _fetch_zone(db, zone_id)
    await db.delete(zone)
    await db.commit()


# ---------------------------------------------------------------------------
# GET /zones/{zone_id}/readings — recent sensor readings for a zone
# ---------------------------------------------------------------------------
@router.get("/{zone_id}/readings", response_model=list[SensorReadingResponse])
async def get_zone_readings(
    zone_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000, description="Max readings to return")] = 100,
    sensor_id: Annotated[uuid.UUID | None, Query(description="Filter by specific sensor")] = None,
) -> list[SensorReadingResponse]:
    """Return the most recent sensor readings for all sensors in a zone."""
    # Verify zone exists
    await _fetch_zone(db, zone_id)

    # Get sensor IDs belonging to this zone
    sensor_stmt = select(Sensor.id).where(Sensor.zone_id == zone_id)
    if sensor_id is not None:
        sensor_stmt = sensor_stmt.where(Sensor.id == sensor_id)
    sensor_result = await db.execute(sensor_stmt)
    sensor_ids = [row[0] for row in sensor_result.all()]

    if not sensor_ids:
        return []

    readings_stmt = (
        select(SensorReading)
        .where(SensorReading.sensor_id.in_(sensor_ids))
        .order_by(SensorReading.recorded_at.desc())
        .limit(limit)
    )
    result = await db.execute(readings_stmt)
    readings = result.scalars().all()
    return [SensorReadingResponse.model_validate(r) for r in readings]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _fetch_zone(db: AsyncSession, zone_id: uuid.UUID) -> Zone:
    """Load a zone with relationships or raise 404."""
    stmt = (
        select(Zone)
        .options(selectinload(Zone.sensors), selectinload(Zone.devices))
        .where(Zone.id == zone_id)
    )
    result = await db.execute(stmt)
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone {zone_id} not found",
        )
    return zone


async def _enrich_zone_response(
    db: AsyncSession, zone: Zone, ha_client: HAClient | None = None
) -> ZoneResponse:
    """Build a ZoneResponse with latest sensor readings attached."""
    resp = ZoneResponse.model_validate(zone)

    if zone.sensors:
        sensor_ids = [s.id for s in zone.sensors]
        reading_stmt = (
            select(SensorReading)
            .where(SensorReading.sensor_id.in_(sensor_ids))
            .order_by(SensorReading.recorded_at.desc())
            .limit(25)
        )
        reading_result = await db.execute(reading_stmt)
        readings = reading_result.scalars().all()
        for reading in readings:
            if resp.current_temp is None and reading.temperature_c is not None:
                resp.current_temp = reading.temperature_c
            if resp.current_humidity is None and reading.humidity is not None:
                resp.current_humidity = reading.humidity
            if resp.is_occupied is None and reading.presence is not None:
                resp.is_occupied = reading.presence
            if (
                resp.current_temp is not None
                and resp.current_humidity is not None
                and resp.is_occupied is not None
            ):
                break

    # Derive target_temp from the primary thermostat device if available
    if zone.devices:
        thermostat = next(
            (d for d in zone.devices if d.type.value == "thermostat" and d.ha_entity_id),
            None,
        )
        if thermostat and thermostat.capabilities:
            resp.target_temp = thermostat.capabilities.get("target_temp")

    # Fetch live thermostat data from HA — PREFER live data over stale DB readings
    if ha_client and zone.devices:
        for device in zone.devices:
            if device.type.value == "thermostat" and device.ha_entity_id:
                try:
                    ha_unit = await _get_ha_temp_unit(ha_client)
                    state = await ha_client.get_state(device.ha_entity_id)
                    attrs = state.attributes
                    # Current temperature reading from the thermostat (always prefer live)
                    if attrs.get("current_temperature") is not None:
                        raw = float(attrs["current_temperature"])
                        resp.current_temp = _ha_temp_to_celsius(raw, ha_unit)
                    # Target / setpoint temperature (always prefer live)
                    # In heat/cool mode HA uses "temperature"; in heat_cool/auto
                    # mode it uses "target_temp_high" / "target_temp_low" instead.
                    if attrs.get("temperature") is not None:
                        raw = float(attrs["temperature"])
                        resp.target_temp = _ha_temp_to_celsius(raw, ha_unit)
                    elif attrs.get("target_temp_high") is not None:
                        raw = float(attrs["target_temp_high"])
                        resp.target_temp = _ha_temp_to_celsius(raw, ha_unit)
                    elif attrs.get("target_temp_low") is not None:
                        raw = float(attrs["target_temp_low"])
                        resp.target_temp = _ha_temp_to_celsius(raw, ha_unit)
                    logger.debug(
                        "Zone %s live HA data: current_temp=%.1f, target_temp=%.1f (HA unit=%s)",
                        zone.name,
                        resp.current_temp if resp.current_temp is not None else 0,
                        resp.target_temp if resp.target_temp is not None else 0,
                        ha_unit,
                    )
                    break
                except (HAClientError, Exception) as exc:
                    logger.debug("Could not fetch HA state for %s: %s", device.ha_entity_id, exc)

    return resp


__all__ = ["router"]
