"""Sensor CRUD API routes for ClimateIQ."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.models.database import Sensor, Zone
from backend.models.enums import SensorType
from backend.models.schemas import SensorCreate, SensorResponse, SensorUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


class BulkSensorItem(BaseModel):
    name: str
    type: SensorType
    ha_entity_id: str
    manufacturer: str = ""
    model: str = ""


# ---------------------------------------------------------------------------
# GET /sensors — list all sensors
# ---------------------------------------------------------------------------
@router.get("", response_model=list[SensorResponse])
async def list_sensors(
    db: Annotated[AsyncSession, Depends(get_db)],
    zone_id: Annotated[uuid.UUID | None, Query(description="Filter by zone")] = None,
    sensor_type: Annotated[
        SensorType | None,
        Query(alias="type", description="Filter by sensor type"),
    ] = None,
) -> list[SensorResponse]:
    """Return all sensors, optionally filtered by zone or type."""
    stmt = select(Sensor).order_by(Sensor.name)

    if zone_id is not None:
        stmt = stmt.where(Sensor.zone_id == zone_id)
    if sensor_type is not None:
        stmt = stmt.where(Sensor.type == sensor_type)

    result = await db.execute(stmt)
    sensors = result.scalars().all()
    return [SensorResponse.model_validate(s) for s in sensors]


# ---------------------------------------------------------------------------
# GET /sensors/{sensor_id} — single sensor detail
# ---------------------------------------------------------------------------
@router.get("/{sensor_id}", response_model=SensorResponse)
async def get_sensor(
    sensor_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SensorResponse:
    """Return a single sensor by ID."""
    sensor = await _fetch_sensor(db, sensor_id)
    return SensorResponse.model_validate(sensor)


# ---------------------------------------------------------------------------
# POST /sensors — create a new sensor
# ---------------------------------------------------------------------------
@router.post("", response_model=SensorResponse, status_code=status.HTTP_201_CREATED)
async def create_sensor(
    payload: SensorCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SensorResponse:
    """Create a new sensor attached to a zone."""
    # Verify the target zone exists
    zone_result = await db.execute(select(Zone).where(Zone.id == payload.zone_id))
    if zone_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone {payload.zone_id} not found",
        )

    sensor = Sensor(**payload.model_dump())
    db.add(sensor)
    await db.commit()
    await db.refresh(sensor)

    # Dynamically add the new entity to the running WS filter so HA
    # state_changed events for this sensor are not silently dropped.
    if sensor.ha_entity_id:
        try:
            from backend.api.main import app_state
            if app_state.ha_ws and hasattr(app_state.ha_ws, "add_entity_to_filter"):
                app_state.ha_ws.add_entity_to_filter(sensor.ha_entity_id)
        except Exception:
            logger.debug("Could not add entity to WS filter (non-fatal)", exc_info=True)

    return SensorResponse.model_validate(sensor)


# ---------------------------------------------------------------------------
# POST /sensors/bulk — create multiple sensors at once
# ---------------------------------------------------------------------------
@router.post("/bulk", response_model=list[SensorResponse], status_code=status.HTTP_201_CREATED)
async def create_sensors_bulk(
    zone_id: uuid.UUID,
    items: list[BulkSensorItem],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[SensorResponse]:
    """Create multiple sensors at once (e.g. from an HA device selection)."""
    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No sensors provided",
        )

    # Verify the target zone exists
    zone_result = await db.execute(select(Zone).where(Zone.id == zone_id))
    if zone_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone {zone_id} not found",
        )

    # Check for duplicate ha_entity_ids already in this zone
    existing_result = await db.execute(
        select(Sensor.ha_entity_id).where(
            Sensor.zone_id == zone_id,
            Sensor.ha_entity_id.in_([it.ha_entity_id for it in items]),
        )
    )
    existing_entities = {row[0] for row in existing_result.all()}

    created: list[Sensor] = []
    for item in items:
        if item.ha_entity_id in existing_entities:
            continue  # Skip duplicates silently

        sensor = Sensor(
            zone_id=zone_id,
            name=item.name,
            type=item.type,
            ha_entity_id=item.ha_entity_id,
            manufacturer=item.manufacturer or None,
            model=item.model or None,
        )
        db.add(sensor)
        created.append(sensor)

    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="All provided entities already exist as sensors in this zone",
        )

    await db.commit()
    for sensor in created:
        await db.refresh(sensor)

    # Add all new entities to WS filter
    try:
        from backend.api.main import app_state
        if app_state.ha_ws and hasattr(app_state.ha_ws, "add_entity_to_filter"):
            for sensor in created:
                if sensor.ha_entity_id:
                    app_state.ha_ws.add_entity_to_filter(sensor.ha_entity_id)
    except Exception:
        logger.debug("Could not add entities to WS filter (non-fatal)", exc_info=True)

    return [SensorResponse.model_validate(s) for s in created]


# ---------------------------------------------------------------------------
# PUT /sensors/{sensor_id} — update an existing sensor
# ---------------------------------------------------------------------------
@router.put("/{sensor_id}", response_model=SensorResponse)
async def update_sensor(
    sensor_id: uuid.UUID,
    payload: SensorUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SensorResponse:
    """Partially update a sensor. Only supplied fields are changed."""
    sensor = await _fetch_sensor(db, sensor_id)

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update",
        )

    for key, value in update_data.items():
        setattr(sensor, key, value)

    await db.commit()
    await db.refresh(sensor)
    return SensorResponse.model_validate(sensor)


# ---------------------------------------------------------------------------
# DELETE /sensors/{sensor_id} — delete a sensor (cascades readings)
# ---------------------------------------------------------------------------
@router.delete("/{sensor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sensor(
    sensor_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a sensor and all its readings via cascade."""
    sensor = await _fetch_sensor(db, sensor_id)
    await db.delete(sensor)
    await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _fetch_sensor(db: AsyncSession, sensor_id: uuid.UUID) -> Sensor:
    """Load a sensor or raise 404."""
    result = await db.execute(select(Sensor).where(Sensor.id == sensor_id))
    sensor = result.scalar_one_or_none()
    if sensor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor {sensor_id} not found",
        )
    return sensor


__all__ = ["router"]
