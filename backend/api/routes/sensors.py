"""Sensor CRUD and discovery API routes for ClimateIQ."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.models.database import Sensor, Zone
from backend.models.enums import SensorType
from backend.models.schemas import SensorCreate, SensorResponse, SensorUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


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
    return SensorResponse.model_validate(sensor)


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
# POST /sensors/discover — trigger MQTT device discovery
# ---------------------------------------------------------------------------
@router.post("/discover", response_model=dict[str, Any])
async def discover_sensors(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Trigger MQTT device discovery via zigbee2mqtt bridge.

    Attempts to connect to the configured MQTT broker and request the device
    list from zigbee2mqtt. Returns discovered devices with their capabilities.
    If the broker is unreachable the endpoint returns a structured error.
    """
    from backend.config import get_settings
    from backend.integrations.mqtt_client import MQTTClient

    settings = get_settings()
    broker = settings.mqtt_broker
    port = settings.mqtt_port
    username = settings.mqtt_username or None
    password = settings.mqtt_password or None
    use_tls = settings.mqtt_use_tls

    if not broker:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MQTT broker not configured. Set CLIMATEIQ_MQTT_BROKER.",
        )

    client = MQTTClient(
        broker=broker,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
    )

    try:
        await client.connect()
        devices = await client.discover_devices(force_refresh=True)
        await client.disconnect()
    except Exception as exc:
        logger.exception("MQTT discovery failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MQTT discovery failed: {exc}",
        ) from exc

    # Build a summary of discovered devices with sensor-relevant info
    discovered: list[dict[str, Any]] = []
    for friendly_name, info in devices.items():
        definition = info.get("definition") or {}
        exposes = definition.get("exposes") or []

        # Determine sensor capabilities from exposed features
        capabilities: list[str] = []
        for feature in exposes:
            if isinstance(feature, dict):
                feat_type = feature.get("type", "")
                feat_name = feature.get("name") or feature.get("property") or ""
                if feat_type in ("numeric", "binary", "enum"):
                    capabilities.append(str(feat_name))
                # Nested features (e.g. climate clusters)
                for sub in feature.get("features", []):
                    if isinstance(sub, dict):
                        sub_name = sub.get("name") or sub.get("property") or ""
                        if sub_name:
                            capabilities.append(str(sub_name))

        discovered.append(
            {
                "friendly_name": friendly_name,
                "ieee_address": info.get("ieee_address"),
                "type": info.get("type"),
                "vendor": (definition.get("vendor") or info.get("manufacturer")),
                "model": (definition.get("model") or info.get("model_id")),
                "description": definition.get("description"),
                "capabilities": capabilities,
                "supported": info.get("supported", True),
            }
        )

    return {
        "broker": broker,
        "discovered_count": len(discovered),
        "devices": discovered,
    }


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
