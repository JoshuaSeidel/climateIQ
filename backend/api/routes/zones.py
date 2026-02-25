"""Zone CRUD and readings API routes for ClimateIQ."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.dependencies import get_db
from backend.integrations import HAClient
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
    except Exception:  # noqa: S110
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
    except Exception:  # noqa: S110
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
    from sqlalchemy.exc import IntegrityError

    zone = await _fetch_zone(db, zone_id)
    try:
        await db.delete(zone)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete zone: it is referenced by existing data. Remove references first.",
        ) from exc


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


# ---------------------------------------------------------------------------
# Cached global climate entity state — shared across all zone enrichments
# within a single request cycle to avoid hitting HA N times for N zones.
# ---------------------------------------------------------------------------
_global_climate_cache: dict[str, Any] | None = None
_global_climate_cache_ts: float = 0.0


async def _get_global_climate_state(
    ha_client: HAClient, db: AsyncSession
) -> dict[str, Any] | None:
    """Return cached {current_temp, target_temp, hvac_mode} from the
    global (whole-house) climate entity, or None if unavailable."""
    import time

    global _global_climate_cache, _global_climate_cache_ts

    # Cache for 15 seconds to avoid hammering HA on every zone
    if _global_climate_cache is not None and (time.time() - _global_climate_cache_ts) < 15:
        return _global_climate_cache

    # Find the climate entity ID — check DB settings first, then app config
    from backend.models.database import SystemSetting as _SS

    climate_entity: str | None = None
    result = await db.execute(select(_SS).where(_SS.key == "climate_entities"))
    row = result.scalar_one_or_none()
    if row and row.value:
        raw_val = row.value.get("value", "")
        if raw_val:
            # Comma-separated — take the first one
            climate_entity = raw_val.split(",")[0].strip()

    if not climate_entity:
        from backend.config import SETTINGS as _cfg
        if _cfg.climate_entities:
            climate_entity = _cfg.climate_entities.split(",")[0].strip()

    if not climate_entity:
        return None

    try:
        ha_unit = await _get_ha_temp_unit(ha_client)
        state = await ha_client.get_state(climate_entity)
        attrs = state.attributes
        hvac_mode = (state.state or "").lower()

        current_temp: float | None = None
        target_temp: float | None = None

        if attrs.get("current_temperature") is not None:
            current_temp = _ha_temp_to_celsius(float(attrs["current_temperature"]), ha_unit)

        # Resolve target temp based on HVAC mode (Ecobee pattern)
        if attrs.get("temperature") is not None:
            target_temp = _ha_temp_to_celsius(float(attrs["temperature"]), ha_unit)
        elif hvac_mode in ("heat", "auto", "heat_cool"):
            if attrs.get("target_temp_low") is not None:
                target_temp = _ha_temp_to_celsius(float(attrs["target_temp_low"]), ha_unit)
        elif hvac_mode == "cool":
            if attrs.get("target_temp_high") is not None:
                target_temp = _ha_temp_to_celsius(float(attrs["target_temp_high"]), ha_unit)
        # Fallback
        if target_temp is None:
            for key in ("target_temp_low", "target_temp_high"):
                if attrs.get(key) is not None:
                    target_temp = _ha_temp_to_celsius(float(attrs[key]), ha_unit)
                    break

        _global_climate_cache = {
            "current_temp": current_temp,
            "target_temp": target_temp,
            "hvac_mode": hvac_mode,
            "entity_id": climate_entity,
        }
        _global_climate_cache_ts = time.time()
        logger.info(
            "Global climate %s: current=%.1f°C, target=%s°C, mode=%s (HA unit=%s)",
            climate_entity,
            current_temp if current_temp is not None else 0,
            f"{target_temp:.1f}" if target_temp is not None else "None",
            hvac_mode,
            ha_unit,
        )
        return _global_climate_cache
    except Exception as exc:
        logger.warning("Failed to fetch global climate entity %s: %s", climate_entity, exc)
        return None


async def _enrich_zone_response(
    db: AsyncSession, zone: Zone, ha_client: HAClient | None = None
) -> ZoneResponse:
    """Build a ZoneResponse with latest sensor readings attached."""
    resp = ZoneResponse.model_validate(zone)

    # 1) Fill from per-zone sensor readings in DB.
    #    Use targeted queries per field to find the latest non-null value.
    #    This avoids the problem where a LIMIT-N scan misses infrequent
    #    sensor types (e.g. humidity sensor reports every 10 min but temp
    #    sensors flood the last N rows).
    if zone.sensors:
        sensor_ids = [s.id for s in zone.sensors]
        base = (
            select(SensorReading)
            .where(SensorReading.sensor_id.in_(sensor_ids))
            .order_by(SensorReading.recorded_at.desc())
            .limit(1)
        )

        async def _fetch_col(col_attr: object) -> SensorReading | None:
            result = await db.execute(base.where(col_attr.isnot(None)))  # type: ignore[union-attr]
            return result.scalars().first()

        temp_row, hum_row, pres_row, lux_row = await asyncio.gather(
            _fetch_col(SensorReading.temperature_c),
            _fetch_col(SensorReading.humidity),
            _fetch_col(SensorReading.presence),
            _fetch_col(SensorReading.lux),
        )
        if temp_row is not None and temp_row.temperature_c is not None:
            resp.current_temp = temp_row.temperature_c
        if hum_row is not None and hum_row.humidity is not None:
            resp.current_humidity = hum_row.humidity
        if pres_row is not None and pres_row.presence is not None:
            resp.is_occupied = pres_row.presence
        if lux_row is not None and lux_row.lux is not None:
            resp.current_lux = lux_row.lux

    # 2) Try per-zone thermostat device (if one is linked)
    thermostat_entity: str | None = None
    if zone.devices:
        thermostat = next(
            (d for d in zone.devices if d.type.value == "thermostat" and d.ha_entity_id),
            None,
        )
        if thermostat:
            thermostat_entity = thermostat.ha_entity_id
            if thermostat.capabilities:
                resp.target_temp = thermostat.capabilities.get("target_temp")

    # 3) Fetch live data from HA
    if ha_client:
        if thermostat_entity:
            # Per-zone thermostat device linked — use it directly
            try:
                ha_unit = await _get_ha_temp_unit(ha_client)
                state = await ha_client.get_state(thermostat_entity)
                attrs = state.attributes
                if attrs.get("current_temperature") is not None:
                    resp.current_temp = _ha_temp_to_celsius(float(attrs["current_temperature"]), ha_unit)
                hvac_mode = (state.state or "").lower()
                if attrs.get("temperature") is not None:
                    resp.target_temp = _ha_temp_to_celsius(float(attrs["temperature"]), ha_unit)
                elif hvac_mode in ("heat", "auto", "heat_cool"):
                    if attrs.get("target_temp_low") is not None:
                        resp.target_temp = _ha_temp_to_celsius(float(attrs["target_temp_low"]), ha_unit)
                elif hvac_mode == "cool":
                    if attrs.get("target_temp_high") is not None:
                        resp.target_temp = _ha_temp_to_celsius(float(attrs["target_temp_high"]), ha_unit)
                if resp.target_temp is None:
                    for key in ("target_temp_low", "target_temp_high"):
                        if attrs.get(key) is not None:
                            resp.target_temp = _ha_temp_to_celsius(float(attrs[key]), ha_unit)
                            break
            except Exception as exc:
                logger.debug("Could not fetch per-zone thermostat %s: %s", thermostat_entity, exc)
        else:
            # No per-zone thermostat — use global (whole-house) climate entity
            # ONLY for target setpoint. Current temp must come from per-zone
            # sensors — the thermostat's reading is just the hallway/unit temp.
            climate = await _get_global_climate_state(ha_client, db)
            if climate:
                if climate["target_temp"] is not None:
                    resp.target_temp = climate["target_temp"]

    return resp


__all__ = ["router"]
