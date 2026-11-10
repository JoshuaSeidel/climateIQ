"""Device CRUD and action execution API routes for ClimateIQ."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.models.database import Device, DeviceAction, Zone
from backend.models.enums import ActionType, ControlMethod, DeviceType, TriggerType
from backend.models.schemas import DeviceActionResponse, DeviceCreate, DeviceResponse, DeviceUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas specific to this module
# ---------------------------------------------------------------------------
class DeviceActionRequest(BaseModel):
    """Payload for executing a device action."""

    action_type: ActionType
    triggered_by: TriggerType = TriggerType.user_override
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# GET /devices — list all devices
# ---------------------------------------------------------------------------
@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    db: Annotated[AsyncSession, Depends(get_db)],
    zone_id: Annotated[uuid.UUID | None, Query(description="Filter by zone")] = None,
    device_type: Annotated[
        DeviceType | None,
        Query(alias="type", description="Filter by device type"),
    ] = None,
    is_primary: Annotated[bool | None, Query(description="Filter by primary flag")] = None,
) -> list[DeviceResponse]:
    """Return all devices, optionally filtered."""
    stmt = select(Device).order_by(Device.name)

    if zone_id is not None:
        stmt = stmt.where(Device.zone_id == zone_id)
    if device_type is not None:
        stmt = stmt.where(Device.type == device_type)
    if is_primary is not None:
        stmt = stmt.where(Device.is_primary == is_primary)

    result = await db.execute(stmt)
    devices = result.scalars().all()
    return [DeviceResponse.model_validate(d) for d in devices]


# ---------------------------------------------------------------------------
# GET /devices/{device_id} — single device detail
# ---------------------------------------------------------------------------
@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceResponse:
    """Return a single device by ID."""
    device = await _fetch_device(db, device_id)
    return DeviceResponse.model_validate(device)


# ---------------------------------------------------------------------------
# POST /devices — create a new device
# ---------------------------------------------------------------------------
@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceResponse:
    """Create a new device attached to a zone."""
    # Verify the target zone exists
    zone_result = await db.execute(select(Zone).where(Zone.id == payload.zone_id))
    if zone_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone {payload.zone_id} not found",
        )

    device = Device(**payload.model_dump())
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return DeviceResponse.model_validate(device)


# ---------------------------------------------------------------------------
# PUT /devices/{device_id} — update an existing device
# ---------------------------------------------------------------------------
@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: uuid.UUID,
    payload: DeviceUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceResponse:
    """Partially update a device. Only supplied fields are changed."""
    device = await _fetch_device(db, device_id)

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update",
        )

    for key, value in update_data.items():
        setattr(device, key, value)

    await db.commit()
    await db.refresh(device)
    return DeviceResponse.model_validate(device)


# ---------------------------------------------------------------------------
# DELETE /devices/{device_id} — delete a device (cascades actions)
# ---------------------------------------------------------------------------
@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a device and all its action history via cascade."""
    device = await _fetch_device(db, device_id)
    await db.delete(device)
    await db.commit()


# ---------------------------------------------------------------------------
# POST /devices/{device_id}/action — execute a device action
# ---------------------------------------------------------------------------
@router.post("/{device_id}/action", response_model=DeviceActionResponse)
async def execute_device_action(
    device_id: uuid.UUID,
    payload: DeviceActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceActionResponse:
    """Execute an action on a device and record it.

    The action is dispatched based on the device's control_method:
    - ha_service_call: forwards to Home Assistant via REST API
    If the integration is unreachable the action is still recorded with an
    error result so the audit trail is preserved.
    """
    device = await _fetch_device(db, device_id)

    # Validate action is compatible with device capabilities
    _validate_action_for_device(device, payload.action_type)

    # The whole-house thermostat is owned by ClimateIQ's control loops; raw
    # service calls here bypassed overrides, dual setpoints and spec §4.3
    # (no heat_cool).  Use /system/override or /system/mode instead.
    from backend.core.manual_control import is_managed_thermostat

    # Both the device's own entity and any entity named in the parameters.
    if any(
        [
            await is_managed_thermostat(db, str(payload.parameters.get("entity_id") or "")),
            await is_managed_thermostat(db, str(device.ha_entity_id or "")),
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the main thermostat — use /system/override or /system/mode.",
        )

    # Attempt to dispatch the action
    result: dict[str, Any] = {}
    try:
        result = await _dispatch_action(device, payload)
    except Exception as exc:
        logger.exception("Device action dispatch failed for %s", device_id)
        result = {"success": False, "error": str(exc)}

    # Persist the action record regardless of dispatch outcome
    action = DeviceAction(
        device_id=device_id,
        triggered_by=payload.triggered_by,
        action_type=payload.action_type,
        parameters=payload.parameters,
        result=result,
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)

    return DeviceActionResponse.model_validate(action)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _fetch_device(db: AsyncSession, device_id: uuid.UUID) -> Device:
    """Load a device or raise 404."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device {device_id} not found",
        )
    return device


def _validate_action_for_device(device: Device, action_type: ActionType) -> None:
    """Raise 422 if the action doesn't make sense for this device type."""
    # Map device types to their valid action types
    valid_actions: dict[DeviceType, set[ActionType]] = {
        DeviceType.thermostat: {
            ActionType.set_temperature,
            ActionType.set_mode,
            ActionType.turn_on,
            ActionType.turn_off,
        },
        DeviceType.smart_vent: {
            ActionType.set_vent_position,
            ActionType.turn_on,
            ActionType.turn_off,
        },
        DeviceType.blind: {
            ActionType.open_cover,
            ActionType.close_cover,
            ActionType.set_cover_position,
        },
        DeviceType.shade: {
            ActionType.open_cover,
            ActionType.close_cover,
            ActionType.set_cover_position,
        },
        DeviceType.fan: {
            ActionType.turn_on,
            ActionType.turn_off,
            ActionType.set_fan_speed,
        },
        DeviceType.space_heater: {
            ActionType.turn_on,
            ActionType.turn_off,
            ActionType.set_temperature,
        },
        DeviceType.mini_split: {
            ActionType.set_temperature,
            ActionType.set_mode,
            ActionType.turn_on,
            ActionType.turn_off,
            ActionType.set_fan_speed,
        },
        DeviceType.humidifier: {
            ActionType.turn_on,
            ActionType.turn_off,
        },
        DeviceType.dehumidifier: {
            ActionType.turn_on,
            ActionType.turn_off,
        },
    }

    allowed = valid_actions.get(device.type)
    # DeviceType.other allows all actions
    if allowed is not None and action_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Action '{action_type.value}' is not valid for "
                f"device type '{device.type.value}'. "
                f"Allowed: {sorted(a.value for a in allowed)}"
            ),
        )


async def _dispatch_action(
    device: Device,
    payload: DeviceActionRequest,
) -> dict[str, Any]:
    """Dispatch the action to the appropriate integration.

    Returns a result dict with at least a 'success' key.
    """
    if device.control_method == ControlMethod.ha_service_call:
        return await _dispatch_via_home_assistant(device, payload)
    return {"success": False, "error": f"Unknown control method: {device.control_method}"}


async def _dispatch_via_home_assistant(
    device: Device,
    payload: DeviceActionRequest,
) -> dict[str, Any]:
    """Forward the action to Home Assistant."""
    from backend.config import get_settings

    settings = get_settings()
    ha_url = str(settings.home_assistant_url).rstrip("/")
    ha_token = settings.home_assistant_token

    if not ha_token:
        return {"success": False, "error": "Home Assistant token not configured"}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            # HA's unit system decides how a °C temperature must be sent.
            cfg = await client.get(
                f"{ha_url}/api/config",
                headers={"Authorization": f"Bearer {ha_token}"},
            )
            cfg.raise_for_status()
            ha_is_f = cfg.json().get("unit_system", {}).get("temperature") == "\u00b0F"
            try:
                service_domain, service_name, service_data = _build_ha_service_call(
                    device, payload, ha_is_f=ha_is_f
                )
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            resp = await client.post(
                f"{ha_url}/api/services/{service_domain}/{service_name}",
                json=service_data,
                headers={
                    "Authorization": f"Bearer {ha_token}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return {"success": True, "ha_status": resp.status_code, "ha_response": resp.json()}
    except Exception as exc:
        return {"success": False, "error": f"HA call failed: {exc}"}


def _build_ha_service_call(
    device: Device,
    payload: DeviceActionRequest,
    *,
    ha_is_f: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    """Map an ActionType to a Home Assistant service domain/name/data.

    Targets the device's own ``ha_entity_id`` (an explicit ``entity_id``
    parameter wins).  Temperatures are taken in °C (``target_c`` or
    ``temperature``), clamped to the safety range and converted to HA's unit.
    The service domain comes from the entity, so fans, covers and switches
    get the right service.  Raises ValueError on missing input.
    """
    from backend.config import SETTINGS

    params = dict(payload.parameters)
    entity_id = str(params.get("entity_id") or device.ha_entity_id or "").strip()
    if not entity_id or "." not in entity_id:
        raise ValueError("Device has no Home Assistant entity; pass parameters.entity_id")
    entity_domain = entity_id.split(".", 1)[0]

    action = payload.action_type

    if action == ActionType.set_temperature:
        raw = params.get("target_c", params.get("temperature"))
        if raw is None:
            raise ValueError("set_temperature requires parameters.target_c (°C)")
        try:
            temp_c = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("target_c must be a number") from exc
        # Reject, never clamp (spec §5.6).
        if not (SETTINGS.safety_min_temp_c <= temp_c <= SETTINGS.safety_max_temp_c):
            raise ValueError(f"target_c {temp_c:g} is outside the safety range")
        temp_ha = round(temp_c * 9 / 5 + 32, 1) if ha_is_f else round(temp_c, 1)
        return "climate", "set_temperature", {"entity_id": entity_id, "temperature": temp_ha}
    if action == ActionType.set_mode:
        mode = params.get("mode")
        if not mode:
            raise ValueError("set_mode requires parameters.mode")
        return "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode}
    if action in (ActionType.turn_on, ActionType.turn_off):
        return entity_domain, action.value, {"entity_id": entity_id}
    if action in (ActionType.open_cover, ActionType.close_cover, ActionType.set_cover_position):
        data: dict[str, Any] = {"entity_id": entity_id}
        if action == ActionType.set_cover_position:
            data["position"] = max(0, min(100, int(params.get("position", 50))))
        return "cover", action.value, data
    if action == ActionType.set_vent_position:
        return (
            "cover",
            "set_cover_position",
            {"entity_id": entity_id, "position": max(0, min(100, int(params.get("position", 100))))},
        )
    if action == ActionType.set_fan_speed:
        pct = params.get("speed", params.get("percentage"))
        if pct is None:
            raise ValueError("set_fan_speed requires parameters.percentage")
        return "fan", "set_percentage", {"entity_id": entity_id, "percentage": max(0, min(100, int(pct)))}
    return "homeassistant", "toggle", {"entity_id": entity_id}


__all__ = ["router"]
