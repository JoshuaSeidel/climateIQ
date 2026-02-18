"""
ClimateIQ Backend API - Main Entry Point

Production-ready FastAPI application for smart HVAC zone management
with real-time updates, weather integration, and AI-powered control.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.middleware import (
    _VERSION,
    APIKeyMiddleware,
    HAAuthMiddleware,
    IngressMiddleware,
    IngressWebSocketMiddleware,
    RateLimitMiddleware,
    is_ha_addon,
)
from backend.api.routes import api_router
from backend.api.websocket import ConnectionManager
from backend.config import get_settings
from backend.integrations.ha_websocket import HAWebSocketClient
from backend.models.database import close_db, get_session_maker, init_db
from backend.services.notification_service import NotificationService

# Configure logging
settings_instance = get_settings()
logging.basicConfig(
    level=logging.DEBUG if settings_instance.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================================
# Application State
# ============================================================================


class AppState:
    """Centralized application state container."""

    def __init__(self) -> None:
        self.redis_client: redis.Redis | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self.ws_manager: ConnectionManager = ConnectionManager(str(get_settings().redis_url))
        self.ha_ws: HAWebSocketClient | None = None
        self.startup_time: datetime | None = None
        self.is_healthy: bool = False


app_state = AppState()

# Module-level singletons for schedule execution and notifications
_notification_service: NotificationService | None = None
_last_executed_schedules: set[str] = set()


# ============================================================================
# Background Tasks
# ============================================================================


async def poll_zone_status() -> None:
    """Periodically poll zone status and broadcast to WebSocket clients."""
    try:
        # Get zone data from database
        session_maker = get_session_maker()
        async with session_maker() as db:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            from backend.models.database import Zone

            stmt = select(Zone).options(
                selectinload(Zone.sensors),
                selectinload(Zone.devices),
            )
            result = await db.execute(stmt)
            zones = result.scalars().unique().all()

            zones_data: list[dict[str, object]] = []
            for zone in zones:
                # Get latest sensor reading for each zone
                latest_reading = None
                if zone.sensors:
                    from backend.models.database import SensorReading

                    reading_stmt = (
                        select(SensorReading)
                        .where(SensorReading.sensor_id.in_([s.id for s in zone.sensors]))
                        .order_by(SensorReading.recorded_at.desc())
                        .limit(1)
                    )
                    reading_result = await db.execute(reading_stmt)
                    latest_reading = reading_result.scalar_one_or_none()

                zones_data.append(
                    {
                        "id": str(zone.id),
                        "name": zone.name,
                        "type": zone.type.value if zone.type else None,
                        "is_active": zone.is_active,
                        "current_temp": latest_reading.temperature_c if latest_reading else None,
                        "current_humidity": latest_reading.humidity if latest_reading else None,
                        "sensor_count": len(zone.sensors),
                        "device_count": len(zone.devices),
                    }
                )

            if zones_data:
                await app_state.ws_manager.broadcast(
                    {
                        "type": "zone_update",
                        "data": zones_data,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                logger.debug(f"Broadcast status for {len(zones_data)} zones")
    except Exception as e:
        logger.error(f"Error polling zone status: {e}")


async def poll_weather_data() -> None:
    """Periodically fetch and cache weather data."""
    import json
    from dataclasses import asdict

    from sqlalchemy import select as sa_select

    from backend.models.database import SystemSetting

    try:
        from backend.integrations import HAClient, WeatherService

        settings = settings_instance
        if not settings.home_assistant_token:
            return

        # Read weather_entity from the DB (no request context)
        session_maker = get_session_maker()
        async with session_maker() as db:
            result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "weather_entity")
            )
            row = result.scalar_one_or_none()
            weather_entity: str = row.value.get("value", "") if row else ""

        if not weather_entity:
            logger.debug("No weather entity configured, skipping poll")
            return

        ha_client = HAClient(
            url=str(settings.home_assistant_url), token=settings.home_assistant_token
        )
        await ha_client.connect()
        weather_service = WeatherService(ha_client, weather_entity=weather_entity)
        weather_data = await weather_service.get_current()

        if weather_data:
            data_dict = asdict(weather_data)
            data_dict.pop("ozone", None)
            fetched_at = datetime.now(UTC).isoformat()

            # Cache in Redis as proper JSON with timestamp
            if app_state.redis_client:
                cache_payload = json.dumps({"fetched_at": fetched_at, "data": data_dict})
                await app_state.redis_client.setex(
                    "weather:current",
                    3600,  # 1 hour hard TTL
                    cache_payload,
                )

            await app_state.ws_manager.broadcast(
                {
                    "type": "weather_update",
                    "data": data_dict,
                    "timestamp": fetched_at,
                }
            )
            logger.debug("Weather data updated and broadcast")
    except Exception as e:
        logger.error(f"Error polling weather data: {e}")


async def cleanup_stale_connections() -> None:
    """Periodically clean up stale WebSocket connections."""
    try:
        stale_count = await app_state.ws_manager.cleanup_stale()
        if stale_count > 0:
            logger.info(f"Cleaned up {stale_count} stale WebSocket connections")
    except Exception as e:
        logger.error(f"Error cleaning up connections: {e}")


async def cleanup_old_readings() -> None:
    """Remove sensor readings older than the retention period.

    Raw readings older than 90 days are deleted.  Aggregated data
    (continuous aggregates in TimescaleDB) is kept longer.
    """
    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            from datetime import timedelta

            from sqlalchemy import text

            cutoff = datetime.now(UTC) - timedelta(days=90)

            # Delete old raw readings (aggregates are kept by TimescaleDB)
            result = await db.execute(
                text("DELETE FROM sensor_readings WHERE recorded_at < :cutoff").bindparams(
                    cutoff=cutoff
                )
            )
            await db.commit()

            deleted = getattr(result, "rowcount", 0)
            if deleted and deleted > 0:
                logger.info(
                    "Data retention: deleted %d sensor readings older than 90 days", deleted
                )
    except Exception as e:
        logger.error(f"Error in data retention cleanup: {e}")


async def check_sensor_health() -> None:
    """Check for offline or malfunctioning sensors."""
    from datetime import timedelta

    from backend.models.database import Sensor

    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            from sqlalchemy import select

            # Find sensors that haven't reported in 30 minutes
            stale_threshold = datetime.now(UTC) - timedelta(minutes=30)
            result = await db.execute(
                select(Sensor).where(
                    Sensor.is_active.is_(True),
                    Sensor.last_seen.isnot(None),
                    Sensor.last_seen < stale_threshold,
                )
            )
            stale_sensors = result.scalars().all()

            for sensor in stale_sensors:
                logger.warning(
                    "Sensor offline: %s (last seen: %s)",
                    sensor.name,
                    sensor.last_seen,
                )
                # Broadcast alert to frontend
                await app_state.ws_manager.broadcast(
                    {
                        "type": "sensor_alert",
                        "alert": "offline",
                        "sensor_id": str(sensor.id),
                        "sensor_name": sensor.name,
                        "last_seen": sensor.last_seen.isoformat() if sensor.last_seen else None,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

                # Send HA notification for offline sensor
                if _notification_service:
                    try:
                        # Load zone name for the notification message
                        zone_name = "unknown zone"
                        if sensor.zone_id:
                            from backend.models.database import Zone as _Zone

                            zone_result = await db.execute(
                                select(_Zone).where(_Zone.id == sensor.zone_id)
                            )
                            zone_obj = zone_result.scalar_one_or_none()
                            if zone_obj:
                                zone_name = zone_obj.name

                        # Read notification_target from system_settings
                        from backend.models.database import SystemSetting as _SS

                        notif_result = await db.execute(
                            select(_SS).where(_SS.key == "notification_target")
                        )
                        notif_row = notif_result.scalar_one_or_none()
                        notif_target = None
                        if notif_row and notif_row.value:
                            notif_target = notif_row.value.get("value") or None

                        await _notification_service.send_ha_notification(
                            title=f"Sensor Offline: {sensor.name}",
                            message=f"{sensor.name} in {zone_name} hasn't reported in 30+ minutes",
                            target=notif_target,
                        )
                    except Exception as notif_err:
                        logger.warning("Sensor offline notification failed: %s", notif_err)

            if stale_sensors:
                logger.info("Sensor health check: %d sensors offline", len(stale_sensors))
    except Exception as e:
        logger.error(f"Error checking sensor health: {e}")


# ============================================================================
# Schedule Execution
# ============================================================================


async def execute_schedules() -> None:
    """Check enabled schedules and fire any whose start_time matches now."""
    global _last_executed_schedules

    from datetime import timedelta

    from sqlalchemy import select as sa_select

    from backend.models.database import Schedule, SystemSetting

    try:
        import backend.api.dependencies as _deps

        ha_client = _deps._ha_client
        if ha_client is None:
            logger.debug("No HA client available, skipping schedule execution")
            return

        session_maker = get_session_maker()
        async with session_maker() as db:
            # Fetch all enabled schedules
            result = await db.execute(
                sa_select(Schedule).where(Schedule.is_enabled.is_(True))
            )
            schedules = result.scalars().all()

            if not schedules:
                return

            now_utc = datetime.now(UTC)
            current_dow = now_utc.weekday()  # 0=Monday … 6=Sunday
            current_time_str = now_utc.strftime("%H:%M")

            # Determine the climate entity to target
            climate_entity: str | None = None

            # Try system_settings KV table first
            setting_result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "climate_entities")
            )
            setting_row = setting_result.scalar_one_or_none()
            if setting_row and setting_row.value:
                val = setting_row.value.get("value", "")
                if isinstance(val, str) and val.strip():
                    # Take the first entity if comma-separated
                    climate_entity = val.strip().split(",")[0].strip()

            # Fall back to config
            if not climate_entity:
                _climate_cfg = settings_instance.climate_entities.strip()
                if _climate_cfg:
                    climate_entity = _climate_cfg.split(",")[0].strip()

            if not climate_entity:
                logger.debug("No climate entity configured, skipping schedule execution")
                return

            # Read notification_target from system_settings
            notif_target: str | None = None
            notif_result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "notification_target")
            )
            notif_row = notif_result.scalar_one_or_none()
            if notif_row and notif_row.value:
                notif_target = notif_row.value.get("value") or None

            # Check temperature unit — HA set_temperature passes through raw,
            # so convert C→F if the system is configured for Fahrenheit.
            temp_unit = settings_instance.temperature_unit.upper()

            for schedule in schedules:
                # Check day of week
                if current_dow not in (schedule.days_of_week or []):
                    continue

                # Check time window (within 2 minutes of start_time)
                try:
                    sched_hour, sched_min = map(int, schedule.start_time.split(":"))
                    sched_dt = now_utc.replace(
                        hour=sched_hour, minute=sched_min, second=0, microsecond=0
                    )
                    delta = abs((now_utc - sched_dt).total_seconds())
                    if delta > 120:  # 2-minute window
                        continue
                except (ValueError, AttributeError):
                    logger.warning("Invalid start_time '%s' on schedule %s", schedule.start_time, schedule.id)
                    continue

                # Dedup: don't re-execute within the same occurrence window
                exec_key = f"{schedule.id}:{schedule.start_time}:{now_utc.strftime('%Y-%m-%d')}"
                if exec_key in _last_executed_schedules:
                    continue

                # Convert temperature if needed
                target_temp = schedule.target_temp_c
                if temp_unit == "F":
                    target_temp = round(schedule.target_temp_c * 9 / 5 + 32, 1)

                # Fire the schedule
                try:
                    await ha_client.set_temperature(climate_entity, target_temp)
                    _last_executed_schedules.add(exec_key)

                    # Determine zone name for logging/notification
                    zone_name = "All zones"
                    if schedule.zone_id:
                        from backend.models.database import Zone

                        zone_result = await db.execute(
                            sa_select(Zone).where(Zone.id == schedule.zone_id)
                        )
                        zone = zone_result.scalar_one_or_none()
                        if zone:
                            zone_name = zone.name

                    temp_display = f"{target_temp:.1f}°{'F' if temp_unit == 'F' else 'C'}"
                    logger.info(
                        "Schedule executed: '%s' → %s set to %s (entity: %s)",
                        schedule.name,
                        zone_name,
                        temp_display,
                        climate_entity,
                    )

                    # Send notification
                    if _notification_service:
                        try:
                            await _notification_service.send_ha_notification(
                                title=f"Schedule Activated: {schedule.name}",
                                message=f"{zone_name}: Target set to {temp_display} at {current_time_str}",
                                target=notif_target,
                            )
                        except Exception as notif_err:
                            logger.warning("Schedule notification failed: %s", notif_err)

                    # Record device action if possible
                    try:
                        from backend.models.database import Device, DeviceAction

                        device_result = await db.execute(
                            sa_select(Device).where(
                                Device.ha_entity_id == climate_entity
                            ).limit(1)
                        )
                        device = device_result.scalar_one_or_none()
                        if device:
                            from backend.models.enums import ActionType, TriggerType

                            action = DeviceAction(
                                device_id=device.id,
                                zone_id=schedule.zone_id,
                                triggered_by=TriggerType.schedule,
                                action_type=ActionType.set_temperature,
                                parameters={
                                    "temperature": target_temp,
                                    "unit": temp_unit,
                                    "schedule_id": str(schedule.id),
                                    "schedule_name": schedule.name,
                                },
                                reasoning=f"Scheduled execution: {schedule.name}",
                            )
                            db.add(action)
                            await db.commit()
                    except Exception as action_err:
                        logger.debug("Could not record device action: %s", action_err)

                except Exception as exec_err:
                    logger.error(
                        "Failed to execute schedule '%s': %s",
                        schedule.name,
                        exec_err,
                    )

        # Prune old dedup keys (keep only today's)
        today_prefix = now_utc.strftime("%Y-%m-%d")
        _last_executed_schedules = {
            k for k in _last_executed_schedules if k.endswith(today_prefix)
        }

    except Exception as e:
        logger.error("Error in schedule execution: %s", e)


# ============================================================================
# Follow-Me Mode Execution
# ============================================================================


async def execute_follow_me_mode() -> None:
    """Adjust thermostat based on zone occupancy (Follow-Me mode).

    Runs every 90 seconds.  Only active when ``SystemConfig.current_mode``
    is ``follow_me``.
    """
    from datetime import timedelta

    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import selectinload

    from backend.models.database import (
        Device,
        DeviceAction,
        SensorReading,
        SystemConfig,
        SystemSetting,
        Zone,
    )
    from backend.models.enums import ActionType, SystemMode, TriggerType

    try:
        import backend.api.dependencies as _deps

        ha_client = _deps._ha_client
        if ha_client is None:
            logger.debug("No HA client available, skipping follow-me execution")
            return

        session_maker = get_session_maker()
        async with session_maker() as db:
            # ── Check current mode ──────────────────────────────────────
            cfg_result = await db.execute(sa_select(SystemConfig).limit(1))
            config = cfg_result.scalar_one_or_none()
            if config is None or config.current_mode != SystemMode.follow_me:
                return

            # ── Determine climate entity ────────────────────────────────
            climate_entity: str | None = None

            setting_result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "climate_entities")
            )
            setting_row = setting_result.scalar_one_or_none()
            if setting_row and setting_row.value:
                val = setting_row.value.get("value", "")
                if isinstance(val, str) and val.strip():
                    climate_entity = val.strip().split(",")[0].strip()

            if not climate_entity:
                _climate_cfg = settings_instance.climate_entities.strip()
                if _climate_cfg:
                    climate_entity = _climate_cfg.split(",")[0].strip()

            if not climate_entity:
                logger.debug("No climate entity configured, skipping follow-me")
                return

            # ── Notification target ─────────────────────────────────────
            notif_target: str | None = None
            notif_result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "notification_target")
            )
            notif_row = notif_result.scalar_one_or_none()
            if notif_row and notif_row.value:
                notif_target = notif_row.value.get("value") or None

            # ── Temperature unit ────────────────────────────────────────
            temp_unit = settings_instance.temperature_unit.upper()

            # ── Fetch active zones with sensors ─────────────────────────
            zone_result = await db.execute(
                sa_select(Zone)
                .options(selectinload(Zone.sensors))
                .where(Zone.is_active.is_(True))
            )
            zones = zone_result.scalars().unique().all()

            if not zones:
                return

            # ── Determine occupancy per zone (last 15 min) ─────────────
            occupancy_cutoff = datetime.now(UTC) - timedelta(minutes=15)
            occupied_zones: list[tuple[Zone, float]] = []  # (zone, target_temp_c)

            for zone in zones:
                if not zone.sensors:
                    continue

                sensor_ids = [s.id for s in zone.sensors]
                reading_result = await db.execute(
                    sa_select(SensorReading)
                    .where(
                        SensorReading.sensor_id.in_(sensor_ids),
                        SensorReading.recorded_at >= occupancy_cutoff,
                        SensorReading.presence.is_(True),
                    )
                    .order_by(SensorReading.recorded_at.desc())
                    .limit(1)
                )
                presence_reading = reading_result.scalar_one_or_none()

                if presence_reading is not None:
                    # Extract comfort preference target temp
                    prefs = zone.comfort_preferences or {}
                    target = (
                        prefs.get("target_temp")
                        or prefs.get("ideal_temp")
                        or 21.0
                    )
                    try:
                        target = float(target)
                    except (TypeError, ValueError):
                        target = 21.0
                    occupied_zones.append((zone, target))

            # ── Calculate target temperature ────────────────────────────
            eco_temp_c = 18.0  # away / eco temperature

            if len(occupied_zones) == 1:
                target_temp_c = occupied_zones[0][1]
                zone_names = occupied_zones[0][0].name
            elif len(occupied_zones) > 1:
                target_temp_c = round(
                    sum(t for _, t in occupied_zones) / len(occupied_zones), 1
                )
                zone_names = ", ".join(z.name for z, _ in occupied_zones)
            else:
                target_temp_c = eco_temp_c
                zone_names = "no occupied zones (eco mode)"

            # ── Check if change is needed (> 0.5°C diff) ───────────────
            try:
                state = await ha_client.get_state(climate_entity)
                current_target = state.attributes.get("temperature")
                if current_target is not None:
                    # If HA is in °F, convert current target to °C for comparison
                    current_target_c = float(current_target)
                    if temp_unit == "F":
                        current_target_c = round((current_target_c - 32) * 5 / 9, 2)
                    if abs(current_target_c - target_temp_c) <= 0.5:
                        return  # No meaningful change needed
            except Exception as state_err:
                logger.debug("Could not read current thermostat state: %s", state_err)
                # Proceed anyway — we'll set the temperature

            # ── Convert and apply ───────────────────────────────────────
            target_for_ha = target_temp_c
            if temp_unit == "F":
                target_for_ha = round(target_temp_c * 9 / 5 + 32, 1)

            await ha_client.set_temperature(climate_entity, target_for_ha)

            temp_display = f"{target_for_ha:.1f}°{'F' if temp_unit == 'F' else 'C'}"
            logger.info(
                "Follow-Me: Set %s to %s for %s",
                climate_entity,
                temp_display,
                zone_names,
            )

            # ── Send notification ───────────────────────────────────────
            if _notification_service:
                try:
                    await _notification_service.send_ha_notification(
                        title="Follow-Me Mode",
                        message=f"Adjusting to {temp_display} for {zone_names}",
                        target=notif_target,
                    )
                except Exception as notif_err:
                    logger.warning("Follow-me notification failed: %s", notif_err)

            # ── Record device action ────────────────────────────────────
            try:
                device_result = await db.execute(
                    sa_select(Device)
                    .where(Device.ha_entity_id == climate_entity)
                    .limit(1)
                )
                device = device_result.scalar_one_or_none()
                if device:
                    action = DeviceAction(
                        device_id=device.id,
                        zone_id=occupied_zones[0][0].id if len(occupied_zones) == 1 else None,
                        triggered_by=TriggerType.follow_me,
                        action_type=ActionType.set_temperature,
                        parameters={
                            "temperature": target_for_ha,
                            "unit": temp_unit,
                            "occupied_zones": [z.name for z, _ in occupied_zones],
                        },
                        reasoning=f"Follow-Me: Adjusting to {temp_display} for {zone_names}",
                        mode=SystemMode.follow_me,
                    )
                    db.add(action)
                    await db.commit()
            except Exception as action_err:
                logger.debug("Could not record follow-me device action: %s", action_err)

    except Exception as e:
        logger.error("Error in follow-me mode execution: %s", e)


# ============================================================================
# Active / AI Mode Execution
# ============================================================================


async def execute_active_mode() -> None:
    """Full AI-driven HVAC control (Active mode).

    Runs every 5 minutes.  Only active when ``SystemConfig.current_mode``
    is ``active``.  Gathers all context, asks the LLM for a recommendation,
    and applies it.
    """
    import json as _json
    import re
    from datetime import timedelta

    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import selectinload

    from backend.models.database import (
        Device,
        DeviceAction,
        Schedule,
        SensorReading,
        SystemConfig,
        SystemSetting,
        Zone,
    )
    from backend.models.enums import ActionType, SystemMode, TriggerType

    try:
        import backend.api.dependencies as _deps

        ha_client = _deps._ha_client
        if ha_client is None:
            logger.debug("No HA client available, skipping active-mode execution")
            return

        session_maker = get_session_maker()
        async with session_maker() as db:
            # ── Check current mode ──────────────────────────────────────
            cfg_result = await db.execute(sa_select(SystemConfig).limit(1))
            config = cfg_result.scalar_one_or_none()
            if config is None or config.current_mode != SystemMode.active:
                return

            # ── Determine climate entity ────────────────────────────────
            climate_entity: str | None = None

            setting_result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "climate_entities")
            )
            setting_row = setting_result.scalar_one_or_none()
            if setting_row and setting_row.value:
                val = setting_row.value.get("value", "")
                if isinstance(val, str) and val.strip():
                    climate_entity = val.strip().split(",")[0].strip()

            if not climate_entity:
                _climate_cfg = settings_instance.climate_entities.strip()
                if _climate_cfg:
                    climate_entity = _climate_cfg.split(",")[0].strip()

            if not climate_entity:
                logger.debug("No climate entity configured, skipping active-mode")
                return

            # ── Notification target ─────────────────────────────────────
            notif_target: str | None = None
            notif_result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "notification_target")
            )
            notif_row = notif_result.scalar_one_or_none()
            if notif_row and notif_row.value:
                notif_target = notif_row.value.get("value") or None

            # ── Temperature unit & safety limits ────────────────────────
            temp_unit = settings_instance.temperature_unit.upper()
            safety_min = settings_instance.safety_min_temp_c
            safety_max = settings_instance.safety_max_temp_c

            # ── Gather zone data ────────────────────────────────────────
            zone_result = await db.execute(
                sa_select(Zone)
                .options(selectinload(Zone.sensors))
                .where(Zone.is_active.is_(True))
            )
            zones = zone_result.scalars().unique().all()

            zone_summaries: list[str] = []
            reading_cutoff = datetime.now(UTC) - timedelta(minutes=15)

            for zone in zones:
                sensor_ids = [s.id for s in zone.sensors] if zone.sensors else []
                latest_reading = None
                if sensor_ids:
                    r_result = await db.execute(
                        sa_select(SensorReading)
                        .where(
                            SensorReading.sensor_id.in_(sensor_ids),
                            SensorReading.recorded_at >= reading_cutoff,
                        )
                        .order_by(SensorReading.recorded_at.desc())
                        .limit(1)
                    )
                    latest_reading = r_result.scalar_one_or_none()

                prefs = zone.comfort_preferences or {}
                target_pref = prefs.get("target_temp") or prefs.get("ideal_temp") or "not set"
                temp_str = f"{latest_reading.temperature_c:.1f}°C" if latest_reading and latest_reading.temperature_c is not None else "N/A"
                hum_str = f"{latest_reading.humidity:.0f}%" if latest_reading and latest_reading.humidity is not None else "N/A"
                occ_str = "occupied" if latest_reading and latest_reading.presence else "unoccupied"

                zone_summaries.append(
                    f"- {zone.name}: temp={temp_str}, humidity={hum_str}, "
                    f"occupancy={occ_str}, comfort_target={target_pref}°C"
                )

            # ── Current thermostat state ────────────────────────────────
            thermostat_info = "unavailable"
            current_target_c: float | None = None
            try:
                state = await ha_client.get_state(climate_entity)
                hvac_mode = state.state
                current_temp = state.attributes.get("current_temperature", "N/A")
                current_target = state.attributes.get("temperature")
                thermostat_info = (
                    f"mode={hvac_mode}, current_temp={current_temp}, "
                    f"target_temp={current_target}"
                )
                if current_target is not None:
                    current_target_c = float(current_target)
                    if temp_unit == "F":
                        current_target_c = round((current_target_c - 32) * 5 / 9, 2)
            except Exception as state_err:
                logger.debug("Could not read thermostat state for AI mode: %s", state_err)

            # ── Weather data from Redis cache ───────────────────────────
            weather_info = "unavailable"
            if app_state.redis_client:
                try:
                    cached = await app_state.redis_client.get("weather:current")
                    if cached:
                        weather_data = _json.loads(cached)
                        w = weather_data.get("data", {})
                        weather_info = (
                            f"temp={w.get('temperature', 'N/A')}°C, "
                            f"humidity={w.get('humidity', 'N/A')}%, "
                            f"condition={w.get('condition', 'N/A')}"
                        )
                except Exception:
                    pass

            # ── Active schedules for today ──────────────────────────────
            now_utc = datetime.now(UTC)
            current_dow = now_utc.weekday()
            schedule_result = await db.execute(
                sa_select(Schedule).where(Schedule.is_enabled.is_(True))
            )
            schedules = schedule_result.scalars().all()
            schedule_summaries: list[str] = []
            for sched in schedules:
                if current_dow in (sched.days_of_week or []):
                    schedule_summaries.append(
                        f"- {sched.name}: {sched.start_time}"
                        f"{'-' + sched.end_time if sched.end_time else ''} "
                        f"target={sched.target_temp_c}°C"
                    )

            # ── Build LLM prompt ────────────────────────────────────────
            zones_text = "\n".join(zone_summaries) if zone_summaries else "No zone data available."
            schedules_text = "\n".join(schedule_summaries) if schedule_summaries else "No active schedules today."

            system_prompt = (
                "You are ClimateIQ's AI HVAC controller. Your job is to recommend "
                "the optimal thermostat target temperature in °C based on the context "
                "provided. Consider occupancy, comfort preferences, weather, energy "
                "efficiency, and current schedules. Be conservative with changes.\n\n"
                "IMPORTANT: You MUST include a line in your response in exactly this "
                "format: RECOMMENDED_TEMP: <number>\n"
                "where <number> is the target temperature in °C (e.g. RECOMMENDED_TEMP: 22.0).\n"
                "Also provide a brief one-sentence reason on a line starting with REASON:."
            )

            user_prompt = (
                f"Current time: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"## Zone Status\n{zones_text}\n\n"
                f"## Thermostat\n{thermostat_info}\n\n"
                f"## Weather\n{weather_info}\n\n"
                f"## Today's Schedules\n{schedules_text}\n\n"
                f"Safety limits: {safety_min}°C – {safety_max}°C\n\n"
                "What target temperature (°C) should the thermostat be set to right now?"
            )

            # ── Call LLM ────────────────────────────────────────────────
            recommended_temp_c: float | None = None
            reason = ""

            try:
                from backend.integrations.llm.provider import LLMProvider

                llm: LLMProvider | None = None
                if settings_instance.anthropic_api_key:
                    llm = LLMProvider(
                        provider="anthropic",
                        api_key=settings_instance.anthropic_api_key,
                    )
                elif settings_instance.openai_api_key:
                    llm = LLMProvider(
                        provider="openai",
                        api_key=settings_instance.openai_api_key,
                    )
                elif settings_instance.gemini_api_key:
                    llm = LLMProvider(
                        provider="gemini",
                        api_key=settings_instance.gemini_api_key,
                    )

                if llm is None:
                    logger.debug("No LLM provider configured, skipping active-mode AI call")
                    return

                response = await llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                content = response.get("content", "")

                # Parse RECOMMENDED_TEMP from response
                temp_match = re.search(r"RECOMMENDED_TEMP:\s*([\d.]+)", content)
                if temp_match:
                    recommended_temp_c = float(temp_match.group(1))

                # Parse REASON from response
                reason_match = re.search(r"REASON:\s*(.+)", content)
                if reason_match:
                    reason = reason_match.group(1).strip()

            except Exception as llm_err:
                logger.error("Active-mode LLM call failed: %s", llm_err)
                # Fall back: do nothing (keep current settings)
                return

            if recommended_temp_c is None:
                logger.warning("Active-mode: LLM did not return a valid temperature")
                return

            # ── Apply safety clamps ─────────────────────────────────────
            recommended_temp_c = max(safety_min, min(safety_max, recommended_temp_c))

            # ── Check if change is meaningful (> 0.5°C diff) ────────────
            if current_target_c is not None and abs(current_target_c - recommended_temp_c) <= 0.5:
                logger.debug(
                    "Active-mode: recommended %.1f°C is within 0.5°C of current %.1f°C, skipping",
                    recommended_temp_c,
                    current_target_c,
                )
                return

            # ── Convert and apply ───────────────────────────────────────
            target_for_ha = recommended_temp_c
            if temp_unit == "F":
                target_for_ha = round(recommended_temp_c * 9 / 5 + 32, 1)

            await ha_client.set_temperature(climate_entity, target_for_ha)

            temp_display = f"{target_for_ha:.1f}°{'F' if temp_unit == 'F' else 'C'}"
            logger.info(
                "Active-mode AI: Set %s to %s — %s",
                climate_entity,
                temp_display,
                reason or "no reason provided",
            )

            # ── Send notification ───────────────────────────────────────
            if _notification_service:
                try:
                    await _notification_service.send_ha_notification(
                        title="AI Mode",
                        message=f"Setting to {temp_display} — {reason or 'AI recommendation'}",
                        target=notif_target,
                    )
                except Exception as notif_err:
                    logger.warning("Active-mode notification failed: %s", notif_err)

            # ── Record device action ────────────────────────────────────
            try:
                device_result = await db.execute(
                    sa_select(Device)
                    .where(Device.ha_entity_id == climate_entity)
                    .limit(1)
                )
                device = device_result.scalar_one_or_none()
                if device:
                    action = DeviceAction(
                        device_id=device.id,
                        zone_id=None,
                        triggered_by=TriggerType.llm_decision,
                        action_type=ActionType.set_temperature,
                        parameters={
                            "temperature": target_for_ha,
                            "unit": temp_unit,
                            "recommended_temp_c": recommended_temp_c,
                        },
                        reasoning=f"AI Mode: {reason or 'LLM recommendation'}",
                        mode=SystemMode.active,
                    )
                    db.add(action)
                    await db.commit()
            except Exception as action_err:
                logger.debug("Could not record active-mode device action: %s", action_err)

    except Exception as e:
        logger.error("Error in active-mode execution: %s", e)


# ============================================================================
# HA WebSocket Sensor Ingestion
# ============================================================================


async def _handle_ha_state_change(change: object) -> None:
    """Ingest a state change from HA WebSocket into sensor_readings and broadcast."""
    from backend.integrations.ha_websocket import HAStateChange
    from backend.models.database import Sensor
    from backend.models.database import SensorReading as SRModel

    if not isinstance(change, HAStateChange):
        return

    # Only persist if we have at least one useful sensor value
    if (
        change.temperature is None
        and change.humidity is None
        and change.lux is None
        and change.presence is None
    ):
        return

    # Validate sensor values are within physically plausible ranges
    if change.temperature is not None and (change.temperature < -40 or change.temperature > 60):
        logger.warning(
            "Rejecting impossible temperature %.1f°C from %s",
            change.temperature,
            change.entity_id,
        )
        return
    if change.humidity is not None and (change.humidity < 0 or change.humidity > 100):
        logger.warning(
            "Rejecting impossible humidity %.1f%% from %s",
            change.humidity,
            change.entity_id,
        )
        return

    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            from sqlalchemy import select

            # Look up sensor by ha_entity_id
            stmt = select(Sensor).where(Sensor.ha_entity_id == change.entity_id)
            result = await db.execute(stmt)
            sensor = result.scalar_one_or_none()

            if sensor is None:
                # Entity not mapped to a sensor — skip (user hasn't registered it)
                logger.debug("Ignoring state change for unmapped entity %s", change.entity_id)
                return

            # Update last_seen
            sensor.last_seen = change.timestamp

            # Create sensor reading
            reading = SRModel(
                sensor_id=sensor.id,
                zone_id=sensor.zone_id,
                recorded_at=change.timestamp,
                temperature_c=change.temperature,
                humidity=change.humidity,
                presence=change.presence,
                lux=change.lux,
                payload=change.attributes,
            )
            db.add(reading)
            await db.commit()

            # Broadcast to frontend
            await app_state.ws_manager.broadcast(
                {
                    "type": "sensor_update",
                    "sensor_id": str(sensor.id),
                    "zone_id": str(sensor.zone_id),
                    "entity_id": change.entity_id,
                    "timestamp": change.timestamp.isoformat(),
                    "data": {
                        "temperature": change.temperature,
                        "humidity": change.humidity,
                        "presence": change.presence,
                        "lux": change.lux,
                    },
                }
            )
    except Exception as e:
        logger.error(f"Error ingesting HA state change for {change.entity_id}: {e}")


# ============================================================================
# Lifecycle Management
# ============================================================================


async def init_redis() -> redis.Redis | None:
    """Initialize Redis connection pool."""
    settings = settings_instance
    try:
        redis_client = redis.from_url(
            str(settings.redis_url),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
        )
        ping_result = redis_client.ping()
        if asyncio.iscoroutine(ping_result):
            await ping_result
        logger.info("Redis connection established")
        return redis_client
    except Exception as e:
        logger.warning(f"Redis connection failed (caching disabled): {e}")
        return None


def init_scheduler() -> AsyncIOScheduler:
    """Initialize the background task scheduler."""
    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 60,
        },
    )

    # Zone status polling - every 30 seconds
    scheduler.add_job(
        poll_zone_status,
        IntervalTrigger(seconds=30),
        id="poll_zone_status",
        name="Poll Zone Status",
        replace_existing=True,
    )

    # Weather data polling - every 15 minutes
    scheduler.add_job(
        poll_weather_data,
        IntervalTrigger(minutes=15),
        id="poll_weather_data",
        name="Poll Weather Data",
        replace_existing=True,
    )

    # Connection cleanup - every 5 minutes
    scheduler.add_job(
        cleanup_stale_connections,
        IntervalTrigger(minutes=5),
        id="cleanup_connections",
        name="Cleanup Stale Connections",
        replace_existing=True,
    )

    # Schedule execution - every 60 seconds
    scheduler.add_job(
        execute_schedules,
        IntervalTrigger(seconds=60),
        id="execute_schedules",
        name="Execute Schedules",
        replace_existing=True,
    )

    # Follow-Me mode execution - every 90 seconds
    scheduler.add_job(
        execute_follow_me_mode,
        IntervalTrigger(seconds=90),
        id="execute_follow_me_mode",
        name="Execute Follow-Me Mode",
        replace_existing=True,
    )

    # Active/AI mode execution - every 5 minutes
    scheduler.add_job(
        execute_active_mode,
        IntervalTrigger(minutes=5),
        id="execute_active_mode",
        name="Execute Active AI Mode",
        replace_existing=True,
    )

    # Sensor health check - every 10 minutes
    scheduler.add_job(
        check_sensor_health,
        IntervalTrigger(minutes=10),
        id="check_sensor_health",
        name="Check Sensor Health",
        replace_existing=True,
    )

    # Data retention cleanup - daily at 3am UTC
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    scheduler.add_job(
        cleanup_old_readings,
        CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="cleanup_old_readings",
        name="Data Retention Cleanup",
        replace_existing=True,
    )

    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan manager for startup and shutdown.
    """
    logger.info("Starting ClimateIQ API...")

    try:
        # Initialize database
        db_url = settings_instance.database_url
        # Mask password in log output
        masked = db_url
        if settings_instance.db_password:
            masked = db_url.replace(settings_instance.db_password, "***")
        logger.info("Connecting to database: %s", masked)
        await init_db()

        # Initialize Redis and share with dependencies
        logger.info("Connecting to Redis...")
        app_state.redis_client = await init_redis()
        from backend.api.dependencies import set_shared_redis

        set_shared_redis(app_state.redis_client)

        await app_state.ws_manager.subscribe_redis()

        # Initialize and start scheduler
        logger.info("Starting background scheduler...")
        app_state.scheduler = init_scheduler()
        app_state.scheduler.start()

        # Connect to Home Assistant WebSocket for real-time sensor data
        settings = settings_instance
        if settings.home_assistant_token:
            logger.info("Connecting to Home Assistant WebSocket...")
            try:
                # Build entity filter from config (comma-separated lists).
                entity_filter: set[str] | None = None
                _climate = settings.climate_entities.strip()
                _sensors = settings.sensor_entities.strip()
                if _climate or _sensors:
                    entity_filter = set()
                    if _climate:
                        entity_filter.update(e.strip() for e in _climate.split(",") if e.strip())
                    if _sensors:
                        entity_filter.update(e.strip() for e in _sensors.split(",") if e.strip())
                    logger.info("Entity filter active: %d entities", len(entity_filter))

                ha_ws = HAWebSocketClient(
                    url=str(settings.home_assistant_url),
                    token=settings.home_assistant_token,
                    entity_filter=entity_filter,
                )
                ha_ws.add_callback(_handle_ha_state_change)
                await ha_ws.connect()
                app_state.ha_ws = ha_ws
            except Exception as e:
                logger.warning(f"HA WebSocket connection failed (sensor ingestion degraded): {e}")

        # Initialize the shared HA REST client so zone enrichment can
        # fetch live thermostat data without requiring a DI-injected dependency.
        if settings.home_assistant_token:
            try:
                from backend.api.dependencies import _ha_client as _existing_ha
                if _existing_ha is None:
                    from backend.integrations import HAClient as _HAClient
                    import backend.api.dependencies as _deps
                    _rest_client = _HAClient(
                        url=str(settings.home_assistant_url),
                        token=settings.home_assistant_token,
                    )
                    await _rest_client.connect()
                    _deps._ha_client = _rest_client
                    logger.info("HA REST client initialized for live thermostat data")
            except Exception as e:
                logger.warning("Failed to initialize HA REST client: %s", e)

        # Initialize NotificationService singleton (requires HA client)
        if settings.home_assistant_token:
            try:
                import backend.api.dependencies as _notif_deps

                if _notif_deps._ha_client is not None:
                    global _notification_service
                    _notification_service = NotificationService(_notif_deps._ha_client)
                    logger.info("NotificationService initialized")
                else:
                    logger.warning("NotificationService not initialized: no HA client available")
            except Exception as e:
                logger.warning("Failed to initialize NotificationService: %s", e)

        # Seed weather_entity from config if set and not already in DB
        if settings_instance.weather_entity:
            try:
                from sqlalchemy import select as sa_select
                from backend.models.database import SystemSetting
                session_maker = get_session_maker()
                async with session_maker() as db:
                    result = await db.execute(
                        sa_select(SystemSetting).where(SystemSetting.key == "weather_entity")
                    )
                    existing = result.scalar_one_or_none()
                    if not existing:
                        db.add(SystemSetting(
                            key="weather_entity",
                            value={"value": settings_instance.weather_entity},
                        ))
                        await db.commit()
                        logger.info("Seeded weather_entity from config: %s", settings_instance.weather_entity)
            except Exception as e:
                logger.warning("Failed to seed weather_entity: %s", e)

        # Record startup time
        app_state.startup_time = datetime.now(UTC)
        app_state.is_healthy = True

        logger.info("ClimateIQ API startup complete")

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        app_state.is_healthy = False
        raise

    yield

    # Shutdown
    logger.info("Shutting down ClimateIQ API...")
    app_state.is_healthy = False

    # Stop scheduler
    if app_state.scheduler:
        logger.info("Stopping background scheduler...")
        app_state.scheduler.shutdown(wait=True)

    # Disconnect HA WebSocket
    if app_state.ha_ws:
        logger.info("Disconnecting HA WebSocket...")
        await app_state.ha_ws.disconnect()
        app_state.ha_ws = None

    # Close all WebSocket connections
    logger.info("Closing WebSocket connections...")
    await app_state.ws_manager.broadcast_all(
        {
            "type": "server_shutdown",
            "message": "Server is shutting down",
        }
    )
    await app_state.ws_manager.shutdown()

    # Close Redis
    if app_state.redis_client:
        logger.info("Closing Redis connection...")
        await app_state.redis_client.close()

    # Close database connections
    logger.info("Closing database connections...")
    await close_db()

    logger.info("ClimateIQ API shutdown complete")


# ============================================================================
# FastAPI Application
# ============================================================================

settings = settings_instance

app = FastAPI(
    title="ClimateIQ API",
    description="""
    ClimateIQ Backend API for Smart HVAC Zone Management.

    ## Features

    * **Zone Management** - Create, read, update, and delete HVAC zones
    * **Real-time Updates** - WebSocket support for live zone status
    * **Weather Integration** - Current weather data and forecasts
    * **Scheduling** - Time-based zone temperature schedules
    * **AI Chat** - Natural language zone control
    * **System Monitoring** - Health checks and system status
    """,
    version=_VERSION,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)


# ============================================================================
# Middleware (applied in reverse order - last added = outermost)
# ============================================================================

# GZip compression (innermost)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Rate limiting (runs before GZip in the middleware stack)
app.add_middleware(RateLimitMiddleware, requests_per_minute=120)

# CORS - in add-on mode HA ingress handles CORS at the proxy level,
# so we keep specific origins only.  Never mix "*" with allow_credentials.
_cors_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time", "X-Ingress-Path"],
    max_age=600,
)

# Ingress path rewriting (works in both addon and standalone mode)
app.add_middleware(IngressMiddleware)

# Home Assistant Ingress support (outermost middleware)
if is_ha_addon():
    logger.info("Home Assistant add-on mode: enabling ingress middleware")
    # HA auth middleware (trusts ingress-authenticated requests)
    app.add_middleware(HAAuthMiddleware, require_auth_for_direct=False)
    # WebSocket ingress middleware (raw ASGI, handles ws:// path rewriting)
    app.add_middleware(IngressWebSocketMiddleware)
else:
    # Standalone mode: optional API key authentication
    if settings.api_key:
        logger.info("API key authentication enabled for standalone mode")
        app.add_middleware(APIKeyMiddleware, api_key=settings.api_key)


@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log all requests with timing and correlation IDs."""
    import time

    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start_time = time.perf_counter()

    request.state.request_id = request_id

    try:
        response = await call_next(request)
        process_time = time.perf_counter() - start_time

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.4f}"

        logger.info(
            f"{request.method} {request.url.path} "
            f"status={response.status_code} "
            f"duration={process_time:.4f}s"
        )

        return response
    except Exception as e:
        logger.error(f"Request failed: {e}", exc_info=True)
        raise


# ============================================================================
# Route Registration
# ============================================================================

app.include_router(api_router)


# ============================================================================
# WebSocket Endpoints
# ============================================================================


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """General WebSocket endpoint for real-time updates."""
    channel = websocket.query_params.get("channel", "general")

    await app_state.ws_manager.connect(websocket, channel)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "channel": channel,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        while True:
            try:
                data = await websocket.receive_json()

                if data.get("type") == "subscribe":
                    new_channel = data.get("channel", "general")
                    await app_state.ws_manager.disconnect(websocket, channel)
                    channel = new_channel
                    await app_state.ws_manager.connect(websocket, channel)
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "channel": channel,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )

                elif data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning(f"WebSocket message error: {e}")

    except WebSocketDisconnect:
        await app_state.ws_manager.disconnect(websocket, channel)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await app_state.ws_manager.disconnect(websocket, channel)


@app.websocket("/ws/zones")
async def websocket_zones(websocket: WebSocket) -> None:
    """Dedicated WebSocket for zone updates."""
    await app_state.ws_manager.connect(websocket, "zones")

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "channel": "zones",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        while True:
            data = await websocket.receive_json()

            if data.get("type") == "request_status":
                zone_id = data.get("zone_id")
                if zone_id:
                    # Fetch and send zone status
                    session_maker = get_session_maker()
                    async with session_maker() as db:
                        from sqlalchemy import select
                        from sqlalchemy.orm import selectinload

                        from backend.models.database import SensorReading, Zone

                        stmt = (
                            select(Zone)
                            .options(selectinload(Zone.sensors))
                            .where(Zone.id == uuid.UUID(zone_id))
                        )
                        result = await db.execute(stmt)
                        zone = result.scalar_one_or_none()

                        if zone and zone.sensors:
                            reading_stmt = (
                                select(SensorReading)
                                .where(SensorReading.sensor_id.in_([s.id for s in zone.sensors]))
                                .order_by(SensorReading.recorded_at.desc())
                                .limit(1)
                            )
                            reading_result = await db.execute(reading_stmt)
                            latest = reading_result.scalar_one_or_none()

                            await websocket.send_json(
                                {
                                    "type": "zone_status",
                                    "zone_id": zone_id,
                                    "data": {
                                        "name": zone.name,
                                        "current_temp": latest.temperature_c if latest else None,
                                        "current_humidity": latest.humidity if latest else None,
                                    },
                                    "timestamp": datetime.now(UTC).isoformat(),
                                }
                            )

    except WebSocketDisconnect:
        await app_state.ws_manager.disconnect(websocket, "zones")


# ============================================================================
# Health Check Endpoints
# ============================================================================


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Basic health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/health/detailed", tags=["Health"])
async def detailed_health_check() -> dict[str, object]:
    """Detailed health check with component status."""
    health_status: dict[str, object] = {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "uptime_seconds": None,
        "components": {},
    }

    # Calculate uptime
    if app_state.startup_time:
        uptime = datetime.now(UTC) - app_state.startup_time
        health_status["uptime_seconds"] = uptime.total_seconds()

    # Check database
    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            from sqlalchemy import text

            await db.execute(text("SELECT 1"))
        components = health_status["components"]
        if isinstance(components, dict):
            components["database"] = {"status": "healthy"}
    except Exception as e:
        components = health_status["components"]
        if isinstance(components, dict):
            components["database"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"

    # Check Redis
    try:
        if app_state.redis_client:
            ping_result = app_state.redis_client.ping()
            if asyncio.iscoroutine(ping_result):
                await ping_result
            components = health_status["components"]
            if isinstance(components, dict):
                components["redis"] = {"status": "healthy"}
        else:
            components = health_status["components"]
            if isinstance(components, dict):
                components["redis"] = {"status": "not_configured"}
    except Exception as e:
        components = health_status["components"]
        if isinstance(components, dict):
            components["redis"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"

    # Check scheduler
    if app_state.scheduler and app_state.scheduler.running:
        jobs = app_state.scheduler.get_jobs()
        components = health_status["components"]
        if isinstance(components, dict):
            components["scheduler"] = {
                "status": "healthy",
                "jobs_count": len(jobs),
            }
    else:
        components = health_status["components"]
        if isinstance(components, dict):
            components["scheduler"] = {"status": "stopped"}
        health_status["status"] = "degraded"

    # WebSocket stats
    components = health_status["components"]
    if isinstance(components, dict):
        components["websockets"] = {
            "status": "healthy",
            "total_connections": app_state.ws_manager.get_connection_count(),
        }

    return health_status


@app.get("/health/ready", tags=["Health"], response_model=None)
async def readiness_check() -> Response:
    """Kubernetes readiness probe."""
    if not app_state.is_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready"},
        )
    return JSONResponse(content={"status": "ready"})


@app.get("/health/live", tags=["Health"])
async def liveness_check() -> dict[str, str]:
    """Kubernetes liveness probe."""
    return {"status": "alive"}


# ============================================================================
# Root / API Info Endpoints
# ============================================================================


@app.get("/api/v1", tags=["Root"])
async def api_root() -> dict[str, object]:
    """API v1 root endpoint."""
    api_prefix = api_router.prefix
    return {
        "version": _VERSION,
        "endpoints": {
            "zones": f"{api_prefix}/zones",
            "sensors": f"{api_prefix}/sensors",
            "devices": f"{api_prefix}/devices",
            "settings": f"{api_prefix}/settings",
            "system": f"{api_prefix}/system",
            "chat": f"{api_prefix}/chat",
            "schedules": f"{api_prefix}/schedules",
            "analytics": f"{api_prefix}/analytics",
        },
    }


# ============================================================================
# Frontend SPA Serving
# ============================================================================

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _FRONTEND_DIR.is_dir():
    # Serve static assets (JS, CSS, images) at /assets
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIR / "assets"),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve the frontend SPA. All non-API routes fall through here
        and return index.html so client-side routing works."""
        file_path = _FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIR / "index.html")

else:
    logger.warning(
        "Frontend dist/ not found at %s — UI will not be served. "
        "Run 'npm run build' in the frontend directory.",
        _FRONTEND_DIR,
    )

    @app.get("/", tags=["Root"])
    async def root_fallback() -> dict[str, object]:
        """API root endpoint (no frontend build available)."""
        return {
            "name": "ClimateIQ API",
            "version": _VERSION,
            "documentation": "/docs" if settings.debug else None,
            "health": "/health",
            "websocket": "/ws",
        }


# ============================================================================
# Exception Handlers
# ============================================================================


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": 500,
                "message": "An internal error occurred" if not settings.debug else str(exc),
                "request_id": getattr(request.state, "request_id", None),
            },
        },
    )


# ============================================================================
# CLI Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.api.main:app",
        host=settings.host,
        port=settings.port,
        loop="asyncio",
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
        access_log=True,
    )
