"""System-level FastAPI routes for ClimateIQ."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import SettingsDep, get_db
from backend.models.database import SystemConfig
from backend.models.enums import SystemMode
from backend.models.schemas import SystemConfigResponse

router = APIRouter()


@router.get("/health", response_model=dict[str, str])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version", response_model=dict[str, str])
async def get_version(settings: SettingsDep) -> dict[str, str]:
    from backend.api.middleware import _VERSION

    return {"name": settings.app_name, "version": _VERSION}


@router.get("/config", response_model=SystemConfigResponse)
async def fetch_config(db: Annotated[AsyncSession, Depends(get_db)]) -> SystemConfigResponse:
    config = await _get_or_create_system_config(db)
    return SystemConfigResponse.model_validate(config)


@router.get("/mode", response_model=dict[str, SystemMode])
async def fetch_mode(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, SystemMode]:
    config = await _get_or_create_system_config(db)
    return {"mode": config.current_mode}


@router.post("/mode", response_model=SystemConfigResponse)
async def update_mode(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemConfigResponse:
    raw_mode = payload.get("mode")
    if not raw_mode:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode_required")
    try:
        new_mode = SystemMode(raw_mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_mode"
        ) from exc
    config = await _get_or_create_system_config(db)
    config.current_mode = new_mode
    config.last_synced_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(config)
    return SystemConfigResponse.model_validate(config)


class LLMSettingsPayload(BaseModel):
    provider: str
    model: str


class LLMSettingsResponse(BaseModel):
    provider: str
    model: str


@router.put("/config/llm", response_model=SystemConfigResponse)
async def update_llm_settings(
    payload: LLMSettingsPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemConfigResponse:
    from backend.integrations.llm.provider import ClimateIQLLMProvider

    provider = payload.provider.strip().lower()
    model = payload.model.strip()
    if provider not in ClimateIQLLMProvider.SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unsupported provider. "
                f"Must be one of: {sorted(ClimateIQLLMProvider.SUPPORTED_PROVIDERS)}"
            ),
        )
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model_required",
        )
    config = await _get_or_create_system_config(db)
    config.llm_settings = {
        "provider": provider,
        "model": model,
    }
    config.last_synced_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(config)
    return SystemConfigResponse.model_validate(config)


@router.get("/config/llm", response_model=LLMSettingsResponse)
async def fetch_llm_settings(db: Annotated[AsyncSession, Depends(get_db)]) -> LLMSettingsResponse:
    config = await _get_or_create_system_config(db)
    llm_settings = dict(config.llm_settings or {})
    provider = str(llm_settings.get("provider") or "")
    model = str(llm_settings.get("model") or "")
    if not provider or not model:
        return LLMSettingsResponse(provider="", model="")
    return LLMSettingsResponse(provider=provider, model=model)


@router.post("/emergency-shutoff", status_code=status.HTTP_200_OK)
async def emergency_shutoff(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Emergency shutoff — turn off ALL HVAC devices immediately.

    This is a safety endpoint that:
    1. Sets system mode to 'learn' (passive observation only)
    2. Sends turn_off commands to all registered devices via HA
    3. Logs the emergency action
    """
    import logging

    from backend.config import get_settings as _get_settings
    from backend.integrations.ha_client import HAClient
    from backend.models.database import Device, SystemConfig

    logger = logging.getLogger(__name__)
    settings = _get_settings()

    # 1. Set system mode to learn (safe/passive)
    result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
    config = result.scalar_one_or_none()
    if config:
        config.current_mode = SystemMode.learn
        await db.commit()

    # 2. Turn off all devices via HA
    devices_off = 0
    errors = []
    if settings.home_assistant_token:
        try:
            async with HAClient(
                str(settings.home_assistant_url), settings.home_assistant_token
            ) as ha:
                device_result = await db.execute(select(Device))
                devices = device_result.scalars().all()
                for device in devices:
                    if device.ha_entity_id:
                        try:
                            domain = device.ha_entity_id.split(".", 1)[0]
                            if domain == "climate":
                                await ha.set_hvac_mode(device.ha_entity_id, "off")
                            else:
                                await ha.turn_off(device.ha_entity_id)
                            devices_off += 1
                        except Exception as e:
                            errors.append(f"{device.ha_entity_id}: {e}")
                            logger.error("Failed to shut off %s: %s", device.ha_entity_id, e)
        except Exception as e:
            errors.append(f"HA connection failed: {e}")
            logger.error("Emergency shutoff HA connection failed: %s", e)
    else:
        errors.append("No HA token configured — cannot send device commands")

    logger.warning(
        "EMERGENCY SHUTOFF executed: %d devices turned off, %d errors",
        devices_off,
        len(errors),
    )

    return {
        "status": "executed",
        "devices_off": devices_off,
        "errors": errors,
        "mode_set_to": "learn",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/test-ha", status_code=status.HTTP_200_OK)
async def test_ha_connection(
    settings: SettingsDep,
) -> dict[str, Any]:
    """Test connectivity to the configured Home Assistant instance.

    Returns connection status, HA version, and discovered entity count.
    """
    import logging

    from backend.integrations.ha_client import HAClient

    logger = logging.getLogger(__name__)

    if not settings.home_assistant_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Home Assistant token configured. Set CLIMATEIQ_HA_TOKEN.",
        )

    try:
        async with HAClient(str(settings.home_assistant_url), settings.home_assistant_token) as ha:
            # Fetch a well-known entity to verify connectivity
            api_status = await ha.get_state("sun.sun")
            entity_id = api_status.entity_id if api_status else ""
            connected = bool(entity_id)
    except Exception as exc:
        logger.warning("HA connection test failed: %s", exc)
        return {
            "connected": False,
            "error": str(exc),
            "url": str(settings.home_assistant_url),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return {
        "connected": connected,
        "url": str(settings.home_assistant_url),
        "entity_check": entity_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /system/quick-action — execute a quick action on the global thermostat
# ---------------------------------------------------------------------------

class QuickActionRequest(BaseModel):
    action: str  # "eco", "away", "boost_heat", "boost_cool", "resume"


class QuickActionResponse(BaseModel):
    success: bool
    message: str
    action: str
    detail: dict[str, Any] | None = None


@router.post("/quick-action", response_model=QuickActionResponse)
async def execute_quick_action(
    payload: QuickActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> QuickActionResponse:
    """Execute a quick action on the global (whole-house) thermostat."""
    import logging as _logging

    from backend.api.dependencies import _ha_client
    from backend.config import SETTINGS
    from backend.integrations.ha_client import HAClientError
    from backend.models.database import SystemSetting

    _logger = _logging.getLogger(__name__)

    if _ha_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Home Assistant client not connected",
        )

    # Find the global climate entity
    climate_entity: str | None = None
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "climate_entities")
    )
    row = result.scalar_one_or_none()
    if row and row.value:
        raw_val = row.value.get("value", "")
        if raw_val:
            climate_entity = raw_val.split(",")[0].strip()

    if not climate_entity and SETTINGS.climate_entities:
        climate_entity = SETTINGS.climate_entities.split(",")[0].strip()

    if not climate_entity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No climate entity configured. Set climate_entities in add-on settings.",
        )

    action = payload.action.lower().strip()

    try:
        state = await _ha_client.get_state(climate_entity)
        attrs = state.attributes
        hvac_modes = attrs.get("hvac_modes", [])

        if action == "eco":
            # Set to eco preset if available, otherwise lower temp
            preset_modes = attrs.get("preset_modes", [])
            if "Away and Eco" in preset_modes:
                await _ha_client.call_service(
                    "climate", "set_preset_mode",
                    data={"preset_mode": "Away and Eco"},
                    target={"entity_id": climate_entity},
                )
                return QuickActionResponse(
                    success=True, message="Set to Eco mode", action=action,
                    detail={"preset_mode": "Away and Eco"},
                )
            # Fallback: just lower target by 3°F (≈1.7°C)
            current_target = attrs.get("temperature") or attrs.get("target_temp_low")
            if current_target is not None:
                new_target = float(current_target) - 3
                await _ha_client.set_temperature(climate_entity, new_target)
                return QuickActionResponse(
                    success=True, message=f"Lowered target to {new_target:.0f}°",
                    action=action, detail={"new_target": new_target},
                )
            return QuickActionResponse(
                success=False, message="Could not determine current target temp",
                action=action,
            )

        elif action == "away":
            preset_modes = attrs.get("preset_modes", [])
            if "away" in [p.lower() for p in preset_modes]:
                # Find the exact case-sensitive name
                away_preset = next(p for p in preset_modes if p.lower() == "away")
                await _ha_client.call_service(
                    "climate", "set_preset_mode",
                    data={"preset_mode": away_preset},
                    target={"entity_id": climate_entity},
                )
                return QuickActionResponse(
                    success=True, message="Set to Away mode", action=action,
                    detail={"preset_mode": away_preset},
                )
            return QuickActionResponse(
                success=False, message="Away preset not available on this thermostat",
                action=action, detail={"available_presets": preset_modes},
            )

        elif action == "boost_heat":
            current_target = attrs.get("temperature") or attrs.get("target_temp_low")
            if current_target is not None:
                new_target = float(current_target) + 2
                await _ha_client.set_temperature(climate_entity, new_target)
                return QuickActionResponse(
                    success=True, message=f"Boosted heat to {new_target:.0f}°",
                    action=action, detail={"new_target": new_target},
                )
            return QuickActionResponse(
                success=False, message="Could not determine current target temp",
                action=action,
            )

        elif action == "boost_cool":
            current_target = attrs.get("temperature") or attrs.get("target_temp_high")
            if current_target is not None:
                new_target = float(current_target) - 2
                await _ha_client.set_temperature(climate_entity, new_target)
                return QuickActionResponse(
                    success=True, message=f"Boosted cooling to {new_target:.0f}°",
                    action=action, detail={"new_target": new_target},
                )
            return QuickActionResponse(
                success=False, message="Could not determine current target temp",
                action=action,
            )

        elif action == "resume":
            await _ha_client.call_service(
                "climate", "set_preset_mode",
                data={"preset_mode": "none"},
                target={"entity_id": climate_entity},
            )
            return QuickActionResponse(
                success=True, message="Resumed normal schedule", action=action,
            )

        else:
            return QuickActionResponse(
                success=False,
                message=f"Unknown action: {action}. Use eco, away, boost_heat, boost_cool, or resume.",
                action=action,
            )

    except HAClientError as exc:
        _logger.error("Quick action failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to execute action: {exc}",
        ) from exc


@router.get("/logic-reference")
async def get_logic_reference() -> dict:
    """Return the ClimateIQ logic reference for UI display and LLM context."""
    return {
        "sections": [
            {
                "id": "architecture",
                "title": "System Architecture",
                "description": "ClimateIQ is a Home Assistant add-on that provides intelligent HVAC management through a React frontend, FastAPI backend, TimescaleDB for time-series data, and Redis for caching/pub-sub.",
                "details": [
                    "All sensor data flows from Home Assistant via WebSocket API — no MQTT.",
                    "One global thermostat (e.g., Ecobee) controls the whole house. Per-zone Zigbee sensors provide room-level temperature, humidity, and occupancy.",
                    "Backend stores all temperatures in Celsius internally. Frontend converts to the user's preferred display unit (°C or °F).",
                    "Home Assistant's unit system is auto-detected via GET /api/config. If HA is in Imperial (°F), raw values are converted to Celsius before storage.",
                    "The system_settings table (key-value store) persists all user preferences across restarts.",
                ]
            },
            {
                "id": "modes",
                "title": "Operating Modes",
                "description": "ClimateIQ has four operating modes that determine how the system interacts with your HVAC.",
                "details": [
                    "Learn Mode: Passive observation only. The system monitors sensor data, occupancy patterns, and temperature preferences without making any HVAC changes. Use this when first setting up to let the system build a baseline.",
                    "Scheduled Mode: The system follows user-created schedules. Each schedule specifies days, times, target temperature, and HVAC mode. The schedule executor checks every 60 seconds and fires matching schedules within a 2-minute window. Schedules can target specific zones or all zones.",
                    "Follow-Me Mode: Occupancy-driven automation. Every 90 seconds, the system checks which zones have detected occupancy (from Zigbee motion/presence sensors). If one zone is occupied, the thermostat targets that zone's comfort preference. If multiple zones are occupied, their preferences are averaged. If no zones are occupied, falls back to eco temperature (18°C / 64°F). Only adjusts if the change exceeds 0.5°C to prevent thermostat chatter.",
                    "Active/AI Mode: Full LLM-driven control. Every 5 minutes, the system gathers all zone data (temps, humidity, occupancy), current weather, thermostat state, today's schedules, and comfort preferences. It sends this context to the configured LLM (Anthropic/OpenAI/etc.) and asks for an optimal temperature recommendation with reasoning. Safety clamps prevent extreme values. The LLM's reasoning is logged for transparency.",
                ]
            },
            {
                "id": "schedules",
                "title": "Schedule System",
                "description": "Schedules let you program temperature changes at specific times and days.",
                "details": [
                    "Each schedule has: name, target zone (or all zones), days of week, start time, optional end time, target temperature, HVAC mode (auto/heat/cool/off), and priority (1-10).",
                    "The executor runs every 60 seconds. It matches the current day and time against enabled schedules using a 2-minute window.",
                    "A dedup mechanism prevents the same schedule from firing twice in the same occurrence — uses a key of schedule_id + start_time + date.",
                    "Higher priority schedules take precedence when conflicts exist. The conflicts endpoint detects overlapping schedules.",
                    "Schedules can also be created through the AI chat using natural language (e.g., 'Set up a weekday morning schedule for 72°F at 7am').",
                ]
            },
            {
                "id": "zones",
                "title": "Zones & Sensors",
                "description": "Zones represent rooms or areas in your home. Each zone can have sensors and devices assigned to it.",
                "details": [
                    "Zones track: current temperature, humidity, occupancy, and comfort preferences (target temp, acceptable ranges).",
                    "Current temperature and humidity come from per-zone Zigbee sensors ONLY — the global thermostat's reading is NOT used for individual zones since it only measures the hallway/unit location.",
                    "The target/setpoint temperature is shared across all zones from the global thermostat (e.g., Ecobee). Since there's one thermostat for the whole house, all zones show the same target.",
                    "When no sensor data exists for a zone, the UI shows '--' instead of fake defaults.",
                    "Comfort preferences per zone (target temp, min/max ranges) are used by Follow-Me mode to determine what temperature to set when that zone is occupied.",
                    "Zone data refreshes every 30 seconds via the polling background task and is broadcast to connected frontends via WebSocket.",
                ]
            },
            {
                "id": "thermostat",
                "title": "Thermostat Integration",
                "description": "ClimateIQ integrates with your thermostat through Home Assistant's climate entity.",
                "details": [
                    "The global thermostat entity is configured via the climate_entities setting (e.g., 'climate.ecobee').",
                    "Ecobee thermostats use target_temp_low in heat mode, target_temp_high in cool mode, and both in auto/heat_cool mode. The system detects the HVAC mode and reads the correct attribute.",
                    "Quick actions (Eco, Away, Boost Heat, Boost Cool) call HA climate services directly — set_preset_mode for Eco/Away, set_temperature for Boost.",
                    "Temperature commands are converted between Celsius and the HA unit system automatically. Backend stores Celsius; if HA is in Fahrenheit, values are converted before sending commands.",
                    "The thermostat state is cached for 15 seconds to avoid excessive API calls when enriching multiple zones.",
                ]
            },
            {
                "id": "notifications",
                "title": "Notifications",
                "description": "ClimateIQ sends push notifications through Home Assistant's mobile app integration.",
                "details": [
                    "Configure your notification target in Settings (e.g., 'mobile_app_joshua_s_iphone'). This maps to HA's notify.mobile_app_* service.",
                    "Notifications are sent for: schedule activations, sensor offline alerts (30+ minutes without data), Follow-Me mode adjustments, and AI mode decisions.",
                    "If no notification target is configured, notifications go to the default HA notify.notify service (persistent notifications in the HA UI).",
                    "Notification history is kept in memory (last 100) for debugging via the NotificationService.",
                ]
            },
            {
                "id": "energy",
                "title": "Energy Monitoring",
                "description": "Energy data is only shown when a real HA energy entity is configured — no fabricated estimates.",
                "details": [
                    "Configure an energy_entity in Settings (e.g., a utility meter sensor from HA).",
                    "The analytics energy endpoint reads live data from the configured HA entity.",
                    "If no energy entity is configured, the energy section on the Dashboard and Analytics is hidden or shows 'Not configured'.",
                    "Energy cost calculations use the energy_cost_per_kwh and currency settings.",
                ]
            },
            {
                "id": "weather",
                "title": "Weather Integration",
                "description": "Weather data is fetched from a Home Assistant weather entity and cached in Redis.",
                "details": [
                    "Configure a weather_entity in Settings (e.g., 'weather.home').",
                    "Weather is polled every 15 minutes and cached in Redis. The Dashboard shows current conditions.",
                    "Weather context is included in the AI chat system prompt and Active mode decisions so the LLM can factor in outdoor conditions.",
                    "Forecast data (12-hour) is available through the weather API and used by the AI mode for proactive adjustments.",
                ]
            },
            {
                "id": "chat",
                "title": "AI Chat Assistant",
                "description": "The chat interface lets you control your HVAC system and ask questions using natural language.",
                "details": [
                    "Supports multiple LLM providers: Anthropic (Claude), OpenAI (GPT), Gemini, Grok, Ollama, and LlamaCPP.",
                    "The LLM has access to tools: set_zone_temperature, get_zone_status, get_weather, create_schedule, set_device_state.",
                    "Quick commands (like 'Set living room to 72') are first parsed by regex for speed, with LLM fallback for complex requests.",
                    "Conversation history is persisted in the database, organized by session. Sessions can be loaded and continued.",
                    "The system prompt includes current zone data, sensor conditions, and this logic reference for full context.",
                ]
            },
            {
                "id": "data",
                "title": "Data & Storage",
                "description": "All data is stored in TimescaleDB (time-series optimized PostgreSQL) with Redis for caching.",
                "details": [
                    "Sensor readings are stored in the sensor_readings table with timestamps. Raw readings older than 90 days are automatically cleaned up (daily at 3am UTC).",
                    "System settings use a key-value table (system_settings) with JSONB values for flexibility.",
                    "Backups can be created and restored through the Settings > Backup tab. Exports include all zones, sensors, devices, schedules, and settings.",
                    "Redis caches: weather data, WebSocket pub/sub for real-time updates, and the global thermostat state (15s TTL).",
                ]
            },
        ]
    }


async def _get_or_create_system_config(session: AsyncSession) -> SystemConfig:
    result = await session.execute(select(SystemConfig).limit(1))
    config = result.scalar_one_or_none()
    if config:
        return config
    config = SystemConfig(current_mode=SystemMode.learn, default_schedule=None, llm_settings={})
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config


__all__ = ["router"]
