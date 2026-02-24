"""System-level FastAPI routes for ClimateIQ."""

from __future__ import annotations

from datetime import UTC, datetime, time
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
    import logging as _logging

    from backend.config import SETTINGS
    from backend.models.database import SystemSetting

    _logger = _logging.getLogger(__name__)

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

    # ------------------------------------------------------------------
    # Ecobee hold management: prevent/restore the Ecobee's internal
    # schedule depending on the new mode.
    # ------------------------------------------------------------------
    try:
        import backend.api.dependencies as _deps

        ha_client = _deps._ha_client
        if ha_client is not None:
            # Resolve the climate entity
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

            if climate_entity:
                if new_mode in (
                    SystemMode.scheduled,
                    SystemMode.active,
                    SystemMode.follow_me,
                ):
                    # Disable Ecobee's own occupancy features so
                    # ClimateIQ has sole control.
                    try:
                        await ha_client.set_ecobee_occupancy_modes(
                            climate_entity, auto_away=False, follow_me=False,
                        )
                        _logger.info(
                            "Ecobee schedule override active: disabled Smart "
                            "Home/Away and Follow Me for mode '%s'",
                            new_mode,
                        )
                    except Exception as eco_err:
                        _logger.debug(
                            "Ecobee occupancy mode update (non-critical): %s",
                            eco_err,
                        )

                elif new_mode == SystemMode.learn:
                    # Restore Ecobee's own schedule and occupancy features.
                    try:
                        await ha_client.delete_ecobee_vacation(
                            climate_entity, "ClimateIQ_Control",
                        )
                    except Exception:  # noqa: S110
                        pass  # May not exist

                    try:
                        await ha_client.resume_ecobee_program(climate_entity)
                    except Exception as resume_err:
                        _logger.debug(
                            "Ecobee resume program (non-critical): %s",
                            resume_err,
                        )

                    try:
                        await ha_client.set_ecobee_occupancy_modes(
                            climate_entity, auto_away=True, follow_me=True,
                        )
                    except Exception as occ_err:
                        _logger.debug(
                            "Ecobee occupancy mode restore (non-critical): %s",
                            occ_err,
                        )

                    _logger.info(
                        "Ecobee schedule control restored for learn mode",
                    )
    except Exception as ecobee_err:
        _logger.debug("Ecobee hold management (non-critical): %s", ecobee_err)

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
            # "Resume" means: find the currently active ClimateIQ
            # schedule and re-apply its target temperature.  All
            # thermostat-specific hold management is handled
            # internally by set_temperature / set_temperature_with_hold.
            from backend.api.routes.schedule import (  # noqa: I001
                _get_user_tz as _sched_get_user_tz,
                parse_time as _sched_parse_time,
            )
            from backend.models.database import Schedule as _Schedule

            user_tz = await _sched_get_user_tz(db)
            now_local = datetime.now(user_tz)
            current_weekday = now_local.weekday()
            current_time = now_local.time()

            # Find all enabled schedules active right now
            sched_result = await db.execute(
                select(_Schedule).where(_Schedule.is_enabled.is_(True))
            )
            all_schedules = list(sched_result.scalars().all())

            active_schedule: _Schedule | None = None
            best_priority = -1

            for sched in all_schedules:
                if current_weekday not in (sched.days_of_week or []):
                    continue
                start_t = _sched_parse_time(sched.start_time)
                end_t = (
                    _sched_parse_time(sched.end_time)
                    if sched.end_time
                    else time(23, 59)
                )
                if end_t < start_t:
                    in_window = current_time >= start_t or current_time <= end_t
                else:
                    in_window = start_t <= current_time <= end_t

                if in_window and sched.priority > best_priority:
                    active_schedule = sched
                    best_priority = sched.priority

            if active_schedule is None:
                return QuickActionResponse(
                    success=False,
                    message="No active schedule right now. Nothing to resume.",
                    action=action,
                )

            # Apply offset compensation
            from backend.core.temp_compensation import apply_offset_compensation

            adjusted_temp_c = active_schedule.target_temp_c
            offset_c = 0.0
            priority_zone_name = None
            try:
                adjusted_temp_c, offset_c, priority_zone_name = await apply_offset_compensation(
                    db, _ha_client, climate_entity,
                    active_schedule.target_temp_c,
                    zone_ids=active_schedule.zone_ids or None,
                )
            except Exception as comp_err:
                _logger.debug("Resume offset compensation (non-critical): %s", comp_err)

            # Convert adjusted temp to HA unit
            ha_config = await _ha_client.get_config()
            ha_temp_unit = (
                ha_config.get("unit_system", {}).get("temperature", "\u00b0C")
            )
            target_temp = adjusted_temp_c
            if ha_temp_unit == "\u00b0F":
                target_temp = round(adjusted_temp_c * 9 / 5 + 32, 1)

            await _ha_client.set_temperature(climate_entity, target_temp)

            # Display uses the original desired temp, not the adjusted one
            display_temp_val = active_schedule.target_temp_c
            if ha_temp_unit == "\u00b0F":
                display_temp_val = round(active_schedule.target_temp_c * 9 / 5 + 32, 1)
            temp_display = (
                f"{display_temp_val:.0f}\u00b0F"
                if ha_temp_unit == "\u00b0F"
                else f"{display_temp_val:.1f}\u00b0C"
            )
            _logger.info(
                "Resumed schedule '%s' — set %s to %s",
                active_schedule.name,
                climate_entity,
                temp_display,
            )

            detail_dict: dict[str, Any] = {
                "schedule_name": active_schedule.name,
                "target_temp": display_temp_val,
            }
            if offset_c and abs(offset_c) > 0.1:
                offset_f = round(offset_c * 9 / 5, 1)
                detail_dict["offset_f"] = offset_f
                detail_dict["adjusted_for_zone"] = priority_zone_name
                _logger.info(
                    "Resume offset compensation: +%.1f F for zone '%s'",
                    offset_f, priority_zone_name or "unknown",
                )

            return QuickActionResponse(
                success=True,
                message=f"Resumed '{active_schedule.name}' ({temp_display})",
                action=action,
                detail=detail_dict,
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
async def get_logic_reference() -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# GET /system/diagnostics — comprehensive integration diagnostics
# ---------------------------------------------------------------------------


class DiagnosticComponent(BaseModel):
    name: str
    status: str  # "ok", "warning", "error", "not_configured"
    message: str | None = None
    details: dict[str, Any] = {}
    latency_ms: float | None = None


class DiagnosticsResponse(BaseModel):
    overall_status: str  # "ok", "degraded", "error"
    timestamp: datetime
    uptime_seconds: float | None = None
    version: str
    components: list[DiagnosticComponent] = []


@router.get("/diagnostics", response_model=DiagnosticsResponse)
async def get_diagnostics(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DiagnosticsResponse:
    """Comprehensive read-only diagnostic check of all system components."""
    import time

    from sqlalchemy import text

    # Lazy imports to avoid circular dependencies
    from backend.api.main import _notification_service, app_state
    from backend.api.middleware import _VERSION
    from backend.config import SETTINGS

    components: list[DiagnosticComponent] = []

    # ------------------------------------------------------------------
    # 1. Database connectivity
    # ------------------------------------------------------------------
    try:
        t0 = time.monotonic()
        await db.execute(text("SELECT 1"))
        latency = (time.monotonic() - t0) * 1000
        components.append(
            DiagnosticComponent(
                name="database",
                status="ok",
                message="Connected",
                latency_ms=round(latency, 2),
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="database",
                status="error",
                message=f"Connection failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 2. TimescaleDB extensions
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('timescaledb', 'uuid-ossp', 'vector')"
            )
        )
        rows = result.fetchall()
        ext_map = {row[0]: row[1] for row in rows}
        components.append(
            DiagnosticComponent(
                name="timescaledb_extensions",
                status="ok" if "timescaledb" in ext_map else "warning",
                message=(
                    f"{len(ext_map)} extension(s) installed"
                    if ext_map
                    else "No target extensions found"
                ),
                details={"extensions": ext_map},
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="timescaledb_extensions",
                status="warning",
                message=f"Could not query extensions: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 3. TimescaleDB hypertables
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            text("SELECT hypertable_name FROM timescaledb_information.hypertables")
        )
        hypertables = [row[0] for row in result.fetchall()]
        components.append(
            DiagnosticComponent(
                name="timescaledb_hypertables",
                status="ok" if hypertables else "warning",
                message=f"{len(hypertables)} hypertable(s)",
                details={"hypertables": hypertables},
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="timescaledb_hypertables",
                status="not_configured",
                message=f"TimescaleDB hypertables not available: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 4. TimescaleDB continuous aggregates
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            text("SELECT view_name FROM timescaledb_information.continuous_aggregates")
        )
        caggs = [row[0] for row in result.fetchall()]
        components.append(
            DiagnosticComponent(
                name="timescaledb_continuous_aggregates",
                status="ok" if caggs else "warning",
                message=f"{len(caggs)} continuous aggregate(s)",
                details={"continuous_aggregates": caggs},
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="timescaledb_continuous_aggregates",
                status="not_configured",
                message=f"Continuous aggregates not available: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 5. Database table counts
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM zones) AS zones, "
                "(SELECT count(*) FROM sensors) AS sensors, "
                "(SELECT count(*) FROM sensor_readings) AS readings, "
                "(SELECT count(*) FROM devices) AS devices, "
                "(SELECT count(*) FROM schedules) AS schedules, "
                "(SELECT count(*) FROM system_settings) AS settings"
            )
        )
        row = result.fetchone()
        if row:
            counts = {
                "zones": row[0],
                "sensors": row[1],
                "sensor_readings": row[2],
                "devices": row[3],
                "schedules": row[4],
                "system_settings": row[5],
            }
        else:
            counts = {}
        components.append(
            DiagnosticComponent(
                name="database_tables",
                status="ok",
                message=f"{sum(counts.values())} total rows across key tables",
                details={"counts": counts},
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="database_tables",
                status="error",
                message=f"Could not query table counts: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 6. Redis connectivity
    # ------------------------------------------------------------------
    try:
        redis_client = app_state.redis_client
        if redis_client is None:
            components.append(
                DiagnosticComponent(
                    name="redis",
                    status="not_configured",
                    message="Redis client not initialised",
                )
            )
        else:
            t0 = time.monotonic()
            await redis_client.ping()  # type: ignore[misc]
            latency = (time.monotonic() - t0) * 1000

            # SET/GET round-trip test
            test_key = "climateiq:diagnostics:probe"
            test_val = f"diag-{datetime.now(UTC).isoformat()}"
            await redis_client.set(test_key, test_val, ex=10)
            readback = await redis_client.get(test_key)
            await redis_client.delete(test_key)

            components.append(
                DiagnosticComponent(
                    name="redis",
                    status="ok",
                    message="Connected — PING + SET/GET OK",
                    latency_ms=round(latency, 2),
                    details={"set_get_match": readback == test_val},
                )
            )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="redis",
                status="error",
                message=f"Redis check failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 7. Home Assistant REST
    # ------------------------------------------------------------------
    try:
        from backend.api.dependencies import _ha_client

        if _ha_client is None:
            components.append(
                DiagnosticComponent(
                    name="home_assistant_rest",
                    status="not_configured",
                    message="HA REST client not initialised",
                )
            )
        else:
            t0 = time.monotonic()
            state = await _ha_client.get_state("sun.sun")
            latency = (time.monotonic() - t0) * 1000
            entity_id = state.entity_id if state else ""
            components.append(
                DiagnosticComponent(
                    name="home_assistant_rest",
                    status="ok" if entity_id else "warning",
                    message=f"sun.sun entity_id={entity_id}" if entity_id else "sun.sun not found",
                    latency_ms=round(latency, 2),
                )
            )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="home_assistant_rest",
                status="error",
                message=f"HA REST check failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 8. Home Assistant WebSocket
    # ------------------------------------------------------------------
    try:
        ha_ws = app_state.ha_ws
        if ha_ws is None:
            components.append(
                DiagnosticComponent(
                    name="home_assistant_websocket",
                    status="not_configured",
                    message="HA WebSocket client not initialised",
                )
            )
        else:
            ws_connected = getattr(ha_ws, "connected", None)
            # .connected is a property returning bool on HAWebSocketClient
            if callable(ws_connected):
                ws_connected = ws_connected()
            components.append(
                DiagnosticComponent(
                    name="home_assistant_websocket",
                    status="ok" if ws_connected else "warning",
                    message="Connected" if ws_connected else "Disconnected",
                    details={"connected": bool(ws_connected)},
                )
            )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="home_assistant_websocket",
                status="error",
                message=f"HA WebSocket check failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 9. Background scheduler
    # ------------------------------------------------------------------
    try:
        scheduler = app_state.scheduler
        if scheduler is None:
            components.append(
                DiagnosticComponent(
                    name="scheduler",
                    status="not_configured",
                    message="Scheduler not initialised",
                )
            )
        else:
            running = getattr(scheduler, "running", False)
            jobs_info: list[dict[str, Any]] = []
            try:
                for job in scheduler.get_jobs():
                    jobs_info.append(
                        {
                            "id": job.id,
                            "next_run_time": (
                                job.next_run_time.isoformat()
                                if job.next_run_time
                                else None
                            ),
                        }
                    )
            except Exception:  # noqa: S110
                pass
            components.append(
                DiagnosticComponent(
                    name="scheduler",
                    status="ok" if running else "warning",
                    message=f"{'Running' if running else 'Stopped'} — {len(jobs_info)} job(s)",
                    details={"running": running, "jobs": jobs_info},
                )
            )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="scheduler",
                status="error",
                message=f"Scheduler check failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 10. Notification service
    # ------------------------------------------------------------------
    try:
        components.append(
            DiagnosticComponent(
                name="notification_service",
                status="ok" if _notification_service is not None else "not_configured",
                message=(
                    "Initialised"
                    if _notification_service is not None
                    else "Not initialised"
                ),
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="notification_service",
                status="error",
                message=f"Notification check failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # 11. LLM providers
    # ------------------------------------------------------------------
    try:
        configured_providers: list[str] = []
        if SETTINGS.anthropic_api_key:
            configured_providers.append("anthropic")
        if SETTINGS.openai_api_key:
            configured_providers.append("openai")
        if SETTINGS.gemini_api_key:
            configured_providers.append("gemini")
        if SETTINGS.grok_api_key:
            configured_providers.append("grok")

        components.append(
            DiagnosticComponent(
                name="llm_providers",
                status="ok" if configured_providers else "not_configured",
                message=(
                    f"{len(configured_providers)} provider(s) configured: "
                    + ", ".join(configured_providers)
                    if configured_providers
                    else "No LLM API keys configured"
                ),
                details={"configured_providers": configured_providers},
            )
        )
    except Exception as exc:
        components.append(
            DiagnosticComponent(
                name="llm_providers",
                status="error",
                message=f"LLM provider check failed: {exc}",
            )
        )

    # ------------------------------------------------------------------
    # Determine overall status
    # ------------------------------------------------------------------
    statuses = {c.status for c in components}
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    # Uptime
    uptime_seconds: float | None = None
    if app_state.startup_time:
        uptime_seconds = (datetime.now(UTC) - app_state.startup_time).total_seconds()

    return DiagnosticsResponse(
        overall_status=overall,
        timestamp=datetime.now(UTC),
        uptime_seconds=round(uptime_seconds, 2) if uptime_seconds is not None else None,
        version=_VERSION,
        components=components,
    )


# ---------------------------------------------------------------------------
# POST /system/override — manual temperature override
# ---------------------------------------------------------------------------


@router.post("/override")
async def set_manual_override(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Set a manual temperature override on the global thermostat."""
    import logging as _logging

    from backend.api.dependencies import _ha_client
    from backend.config import SETTINGS
    from backend.integrations.ha_client import HAClientError
    from backend.models.database import Device, DeviceAction, SystemSetting
    from backend.models.enums import ActionType, TriggerType

    _logger = _logging.getLogger(__name__)

    # 1. Validate required payload fields
    temperature = payload.get("temperature")
    if temperature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="temperature is required",
        )
    display_temp = float(temperature)

    # Accept optional fields (not used yet, but accepted for future use)
    _duration_hours = payload.get("duration_hours")
    _zone_id = payload.get("zone_id")

    if _ha_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Home Assistant client not connected",
        )

    # 2. Get the user's preferred temperature unit from system_settings
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "temperature_unit")
    )
    row = result.scalar_one_or_none()
    user_unit: str = "C"
    if row and row.value:
        raw_unit = row.value.get("value", "C")
        if raw_unit:
            user_unit = str(raw_unit).upper()

    # 3. Get the HA temperature unit from HA config
    ha_config = await _ha_client.get_config()
    ha_temp_unit = ha_config.get("unit_system", {}).get("temperature", "\u00b0C")

    # 4. Convert display temp to HA unit
    temp_for_ha = display_temp
    if user_unit == "F" and ha_temp_unit == "\u00b0C":
        # User sends Fahrenheit, HA expects Celsius
        temp_for_ha = (display_temp - 32) * 5 / 9
    elif user_unit == "C" and ha_temp_unit == "\u00b0F":
        # User sends Celsius, HA expects Fahrenheit
        temp_for_ha = display_temp * 9 / 5 + 32

    # 5. Resolve the climate entity
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

    # 6. Send the temperature command to HA
    try:
        try:
            await _ha_client.set_temperature_with_hold(climate_entity, temp_for_ha)
        except Exception:
            _logger.debug(
                "set_temperature_with_hold failed, falling back to set_temperature"
            )
            await _ha_client.set_temperature(climate_entity, temp_for_ha)
    except HAClientError as exc:
        _logger.error("Manual override failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to set temperature: {exc}",
        ) from exc

    # 7. Record audit trail (best-effort, do not fail the request)
    try:
        dev_result = await db.execute(
            select(Device).where(Device.ha_entity_id == climate_entity)
        )
        device = dev_result.scalar_one_or_none()
        if device:
            action = DeviceAction(
                device_id=device.id,
                triggered_by=TriggerType.user_override,
                action_type=ActionType.set_temperature,
                parameters={"temperature": display_temp, "unit": user_unit},
            )
            db.add(action)
            await db.commit()
    except Exception as audit_err:
        _logger.debug("Audit trail recording (non-critical): %s", audit_err)

    unit_symbol = "\u00b0F" if user_unit == "F" else "\u00b0C"
    return {
        "success": True,
        "message": f"Temperature set to {display_temp}{unit_symbol}",
        "temperature": display_temp,
        "unit": user_unit,
    }


# ---------------------------------------------------------------------------
# GET /system/debug/offset-calculation — debug offset calculation
# ---------------------------------------------------------------------------


@router.get("/debug/offset-calculation")
async def debug_offset_calculation(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Debug endpoint to show what ClimateIQ is calculating for offset compensation."""
    from zoneinfo import ZoneInfo

    from backend.core.temp_compensation import (
        compute_adjusted_setpoint,
        get_avg_zone_temp_c,
        get_max_offset_setting,
        get_thermostat_reading_c,
    )
    from backend.integrations.ha_client import HAClient
    from backend.models.database import Schedule, SystemSetting

    # Get HA client
    ha_client: HAClient | None = None
    try:
        from backend.config import SETTINGS as _cfg

        ha_url = _cfg.ha_url
        ha_token = _cfg.ha_token
        if ha_url and ha_token:
            ha_client = HAClient(ha_url, ha_token)
    except Exception as exc:
        return {"error": f"Could not initialize HA client: {exc}"}

    # Get climate entity
    climate_entity: str | None = None
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "climate_entities"))
    row = result.scalar_one_or_none()
    if row and row.value:
        raw_val = row.value.get("value", "")
        if raw_val:
            climate_entity = raw_val.split(",")[0].strip()

    if not climate_entity:
        from backend.config import SETTINGS as _cfg

        if _cfg.climate_entities:
            climate_entity = _cfg.climate_entities.split(",")[0].strip()

    if not climate_entity:
        return {"error": "No climate entity configured"}

    # Get timezone
    user_tz = ZoneInfo("UTC")
    try:
        tz_result = await db.execute(select(SystemSetting).where(SystemSetting.key == "timezone"))
        tz_row = tz_result.scalar_one_or_none()
        if tz_row and tz_row.value:
            tz_val = tz_row.value.get("value", "")
            if tz_val:
                user_tz = ZoneInfo(tz_val)
    except Exception:
        pass

    # Find active schedule
    now_local = datetime.now(user_tz)
    current_dow = now_local.weekday()
    cur_t = now_local.time()

    result = await db.execute(select(Schedule).where(Schedule.is_enabled == True))  # noqa: E712
    schedules = list(result.scalars().all())

    active_schedule: Schedule | None = None
    for schedule in schedules:
        if current_dow not in (schedule.days_of_week or []):
            continue
        try:
            s_hour, s_min = map(int, schedule.start_time.split(":"))
            start_t = time(s_hour, s_min)
        except (ValueError, AttributeError):
            continue
        end_t = time(23, 59)
        if schedule.end_time:
            try:
                e_hour, e_min = map(int, schedule.end_time.split(":"))
                end_t = time(e_hour, e_min)
            except (ValueError, AttributeError):
                pass
        if end_t < start_t:
            is_in_window = cur_t >= start_t or cur_t <= end_t
        else:
            is_in_window = start_t <= cur_t <= end_t
        if is_in_window:
            if active_schedule is None or schedule.priority > active_schedule.priority:
                active_schedule = schedule

    if not active_schedule:
        return {"error": "No active schedule found"}

    desired_temp_c = active_schedule.target_temp_c
    desired_temp_f = round(desired_temp_c * 9 / 5 + 32, 1)
    zone_ids = active_schedule.zone_ids or None

    # Get zone temperatures
    avg_temp_c, zone_names = await get_avg_zone_temp_c(db, zone_ids, ha_client=ha_client)
    avg_temp_f = round(avg_temp_c * 9 / 5 + 32, 1) if avg_temp_c is not None else None

    # Get thermostat reading
    thermostat_c = await get_thermostat_reading_c(ha_client, climate_entity)
    thermostat_f = round(thermostat_c * 9 / 5 + 32, 1) if thermostat_c is not None else None

    # Compute offset
    if avg_temp_c is None or thermostat_c is None:
        return {
            "error": "Missing temperature data",
            "schedule": {
                "name": active_schedule.name,
                "target_c": desired_temp_c,
                "target_f": desired_temp_f,
                "zone_ids": zone_ids,
            },
            "avg_zone_temp_c": avg_temp_c,
            "avg_zone_temp_f": avg_temp_f,
            "thermostat_reading_c": thermostat_c,
            "thermostat_reading_f": thermostat_f,
            "zone_names": zone_names,
        }

    max_offset_f = await get_max_offset_setting(db)
    adjusted_c, offset_c = await compute_adjusted_setpoint(
        desired_temp_c, thermostat_c, avg_temp_c, max_offset_f
    )
    adjusted_f = round(adjusted_c * 9 / 5 + 32, 1)
    offset_f = round(offset_c * 9 / 5, 1)

    return {
        "schedule": {
            "name": active_schedule.name,
            "id": str(active_schedule.id),
            "target_c": desired_temp_c,
            "target_f": desired_temp_f,
            "zone_ids": zone_ids,
        },
        "zone_data": {
            "avg_temp_c": avg_temp_c,
            "avg_temp_f": avg_temp_f,
            "zone_names": zone_names,
        },
        "thermostat": {
            "reading_c": thermostat_c,
            "reading_f": thermostat_f,
            "entity_id": climate_entity,
        },
        "offset_calculation": {
            "zone_error_c": round(desired_temp_c - avg_temp_c, 2),
            "zone_error_f": round((desired_temp_c - avg_temp_c) * 9 / 5, 2),
            "offset_c": offset_c,
            "offset_f": offset_f,
            "adjusted_c": adjusted_c,
            "adjusted_f": adjusted_f,
            "max_offset_f": max_offset_f,
        },
        "timestamp": now_local.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /system/override — current override status
# ---------------------------------------------------------------------------


@router.get("/override")
async def get_override_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return the current thermostat override status."""
    import logging as _logging

    from backend.api.dependencies import _ha_client
    from backend.config import SETTINGS
    from backend.models.database import SystemSetting

    _logger = _logging.getLogger(__name__)

    if _ha_client is None:
        return {
            "current_temp": None,
            "target_temp": None,
            "hvac_mode": None,
            "preset_mode": None,
            "is_override_active": False,
        }

    # Resolve the climate entity
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
        return {
            "current_temp": None,
            "target_temp": None,
            "hvac_mode": None,
            "preset_mode": None,
            "is_override_active": False,
        }

    try:
        state = await _ha_client.get_state(climate_entity)
        attrs = state.attributes

        current_temp_raw = attrs.get("current_temperature")
        target_temp_raw = attrs.get("temperature")
        hvac_mode = state.state if hasattr(state, "state") else None
        preset_mode = attrs.get("preset_mode")

        # Get the HA temperature unit
        ha_config = await _ha_client.get_config()
        ha_temp_unit = ha_config.get("unit_system", {}).get("temperature", "\u00b0C")

        # Convert HA temps to Celsius first
        current_temp_c: float | None = None
        target_temp_c: float | None = None

        if current_temp_raw is not None:
            current_temp_c = float(current_temp_raw)
            if ha_temp_unit == "\u00b0F":
                current_temp_c = (current_temp_c - 32) * 5 / 9

        if target_temp_raw is not None:
            target_temp_c = float(target_temp_raw)
            if ha_temp_unit == "\u00b0F":
                target_temp_c = (target_temp_c - 32) * 5 / 9

        # Get the user's preferred display unit
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "temperature_unit")
        )
        row = result.scalar_one_or_none()
        user_unit: str = "C"
        if row and row.value:
            raw_unit = row.value.get("value", "C")
            if raw_unit:
                user_unit = str(raw_unit).upper()

        # Convert from Celsius to user's display unit
        display_current_temp: float | None = None
        display_target_temp: float | None = None

        if current_temp_c is not None:
            if user_unit == "F":
                display_current_temp = round(current_temp_c * 9 / 5 + 32, 1)
            else:
                display_current_temp = round(current_temp_c, 1)

        if target_temp_c is not None:
            if user_unit == "F":
                display_target_temp = round(target_temp_c * 9 / 5 + 32, 1)
            else:
                display_target_temp = round(target_temp_c, 1)

        # Determine if override is active.
        # On Ecobee, "temp" means a temperature hold is active -- that
        # is normal ClimateIQ operation (we set a temp, Ecobee shows it
        # as a hold).  Only comfort-profile presets (sleep, away, home,
        # etc.) count as a real override that the user should see.
        _NORMAL_PRESETS = {"none", "", "temp"}
        is_override = bool(
            preset_mode
            and preset_mode.lower() not in _NORMAL_PRESETS
            and preset_mode.strip()
        )

        # Compute offset compensation info for display.
        # Scope to the active schedule's zones so we don't show offset
        # for a zone that isn't part of the current schedule.
        offset_info: dict[str, Any] = {}
        # Pre-declare so they always exist even if an exception fires mid-block.
        schedule_avg_temp: float | None = None
        all_zones_avg_temp: float | None = None
        schedule_target_temp: float | None = None
        schedule_zone_names: str | None = None
        try:
            from backend.api.routes.schedule import (
                _get_user_tz,
                _parse_zone_ids,
                parse_time,
            )
            from backend.core.temp_compensation import (
                get_avg_zone_temp_c,
                get_priority_zone_temp_c,
                get_thermostat_reading_c,
            )
            from backend.models.database import Schedule, Zone as _Zone

            # Find the currently-active schedule using the same helpers as the
            # schedule endpoint — avoids the duplicate/buggy inline logic.
            _best: Schedule | None = None
            active_zone_ids: list[str] | None = None
            try:
                _user_tz = await _get_user_tz(db)
                _now_local = datetime.now(_user_tz)
                _cur_dow = _now_local.weekday()
                _cur_t = _now_local.time()

                _sched_r = await db.execute(
                    select(Schedule).where(Schedule.is_enabled.is_(True))
                )
                for _s in _sched_r.scalars().all():
                    if _cur_dow not in (_s.days_of_week or []):
                        continue
                    try:
                        _st = parse_time(_s.start_time)
                        _et = parse_time(_s.end_time) if _s.end_time else time(23, 59)
                    except (ValueError, AttributeError):
                        continue
                    if _et < _st:
                        _in_window = _cur_t >= _st or _cur_t <= _et
                    else:
                        _in_window = _st <= _cur_t <= _et
                    if _in_window:
                        if _best is None or _s.priority > _best.priority:
                            _best = _s

                if _best and _best.zone_ids:
                    active_zone_ids = _best.zone_ids
            except Exception as _sched_err:
                _logger.warning("Schedule lookup failed in override status: %s", _sched_err)
                # _best stays None; fall through to all-zones averages

            # ── Schedule target temp (what we want the rooms to be) ─────
            schedule_target_c: float | None = None
            if _best is not None:
                schedule_target_c = _best.target_temp_c
                # Resolve zone names from zone_ids
                try:
                    _zone_uuids = _parse_zone_ids(_best)
                    if _zone_uuids:
                        _zr = await db.execute(
                            select(_Zone.name).where(_Zone.id.in_(_zone_uuids))
                        )
                        _znames = [row[0] for row in _zr.all()]
                        if _znames:
                            schedule_zone_names = ", ".join(sorted(_znames))
                except Exception as _zn_err:
                    _logger.warning("Zone name resolution failed: %s", _zn_err)

            # Priority zone temp (for offset calculation, scoped to schedule)
            zone_temp_c, zone_name, _zpri = await get_priority_zone_temp_c(
                db, zone_ids=active_zone_ids, ha_client=_ha_client
            )

            # Schedule zones average -- avg across ALL zones in the schedule
            schedule_avg_c, _sched_names = await get_avg_zone_temp_c(
                db, zone_ids=active_zone_ids, ha_client=_ha_client
            )
            # All active zones average -- avg across every active zone
            all_zones_avg_c, _all_names = await get_avg_zone_temp_c(
                db, ha_client=_ha_client
            )

            thermostat_c = await get_thermostat_reading_c(_ha_client, climate_entity)
            if zone_temp_c is not None and thermostat_c is not None:
                raw_offset_c = thermostat_c - zone_temp_c
                offset_info = {
                    "priority_zone": zone_name,
                    "priority_zone_temp_c": round(zone_temp_c, 1),
                    "thermostat_reading_c": round(thermostat_c, 1),
                    "offset_c": round(raw_offset_c, 1),
                    "offset_f": round(raw_offset_c * 9 / 5, 1),
                }

            # Convert averages to user display unit
            if schedule_avg_c is not None:
                schedule_avg_temp = (
                    round(schedule_avg_c * 9 / 5 + 32, 1) if user_unit == "F"
                    else round(schedule_avg_c, 1)
                )
            if all_zones_avg_c is not None:
                all_zones_avg_temp = (
                    round(all_zones_avg_c * 9 / 5 + 32, 1) if user_unit == "F"
                    else round(all_zones_avg_c, 1)
                )
            if schedule_target_c is not None:
                schedule_target_temp = (
                    round(schedule_target_c * 9 / 5 + 32, 1) if user_unit == "F"
                    else round(schedule_target_c, 1)
                )
        except Exception as _oi_err:
            _logger.warning("Offset info computation failed: %s", _oi_err)

        return {
            "current_temp": display_current_temp,
            "target_temp": display_target_temp,
            "hvac_mode": hvac_mode,
            "preset_mode": preset_mode,
            "is_override_active": is_override,
            "offset_info": offset_info,
            "schedule_avg_temp": schedule_avg_temp,
            "all_zones_avg_temp": all_zones_avg_temp,
            "schedule_target_temp": schedule_target_temp,
            "schedule_zone_names": schedule_zone_names,
        }

    except Exception as exc:
        _logger.error("Failed to get override status: %s", exc)
        return {
            "current_temp": None,
            "target_temp": None,
            "hvac_mode": None,
            "preset_mode": None,
            "is_override_active": False,
            "offset_info": {},
            "schedule_avg_temp": None,
            "all_zones_avg_temp": None,
            "schedule_target_temp": None,
            "schedule_zone_names": None,
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
