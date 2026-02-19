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


# Track which sensors we've already notified about to avoid spamming
# every 10 minutes. Cleared when the sensor comes back online (last_seen updates).
_offline_notified: set[str] = set()


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
            stale_ids = {str(s.id) for s in stale_sensors}

            # Clear notification state for sensors that came back online
            _offline_notified.difference_update(_offline_notified - stale_ids)

            for sensor in stale_sensors:
                sensor_key = str(sensor.id)

                # Always broadcast to frontend (lightweight)
                await app_state.ws_manager.broadcast(
                    {
                        "type": "sensor_alert",
                        "alert": "offline",
                        "sensor_id": sensor_key,
                        "sensor_name": sensor.name,
                        "last_seen": sensor.last_seen.isoformat() if sensor.last_seen else None,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

                # Only send HA push notification ONCE per offline episode
                if sensor_key in _offline_notified:
                    continue
                _offline_notified.add(sensor_key)

                logger.warning(
                    "Sensor offline: %s (last seen: %s)",
                    sensor.name,
                    sensor.last_seen,
                )

                if _notification_service:
                    try:
                        zone_name = "unknown zone"
                        if sensor.zone_id:
                            from backend.models.database import Zone as _Zone

                            zone_result = await db.execute(
                                select(_Zone).where(_Zone.id == sensor.zone_id)
                            )
                            zone_obj = zone_result.scalar_one_or_none()
                            if zone_obj:
                                zone_name = zone_obj.name

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
        logger.error("Error checking sensor health: %s", e)


# ============================================================================
# Schedule Execution
# ============================================================================


async def execute_schedules() -> None:
    """Check enabled schedules and fire any whose start_time matches now."""
    global _last_executed_schedules

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

                    # Determine zone names for logging/notification
                    zone_display = "All zones"
                    raw_zone_ids = schedule.zone_ids or []
                    if raw_zone_ids:
                        from backend.models.database import Zone

                        zone_uuids = []
                        for zid_str in raw_zone_ids:
                            try:
                                zone_uuids.append(uuid.UUID(str(zid_str)))
                            except (ValueError, AttributeError):
                                pass
                        if zone_uuids:
                            zone_result = await db.execute(
                                sa_select(Zone).where(Zone.id.in_(zone_uuids))
                            )
                            zone_names = [z.name for z in zone_result.scalars().all()]
                            if zone_names:
                                zone_display = ", ".join(zone_names)

                    temp_display = f"{target_temp:.1f}°{'F' if temp_unit == 'F' else 'C'}"
                    logger.info(
                        "Schedule executed: '%s' → %s set to %s (entity: %s)",
                        schedule.name,
                        zone_display,
                        temp_display,
                        climate_entity,
                    )

                    # Send notification
                    if _notification_service:
                        try:
                            await _notification_service.send_ha_notification(
                                title=f"Schedule Activated: {schedule.name}",
                                message=f"{zone_display}: Target set to {temp_display} at {current_time_str}",
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
                                zone_id=None,
                                triggered_by=TriggerType.schedule,
                                action_type=ActionType.set_temperature,
                                parameters={
                                    "temperature": target_temp,
                                    "unit": temp_unit,
                                    "schedule_id": str(schedule.id),
                                    "schedule_name": schedule.name,
                                    "zone_ids": [str(zid) for zid in raw_zone_ids],
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
                    # Frontend saves temp_min/temp_max; use midpoint as target
                    prefs = zone.comfort_preferences or {}
                    temp_min = prefs.get("temp_min")
                    temp_max = prefs.get("temp_max")
                    if temp_min is not None and temp_max is not None:
                        try:
                            target = (float(temp_min) + float(temp_max)) / 2.0
                        except (TypeError, ValueError):
                            target = 21.0
                    else:
                        # Legacy fallback
                        target = prefs.get("target_temp") or prefs.get("ideal_temp") or 21.0
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

                # Fetch recent readings (multiple to cover different sensor types)
                temp_val: float | None = None
                hum_val: float | None = None
                lux_val: float | None = None
                occ_val: bool | None = None

                if sensor_ids:
                    r_result = await db.execute(
                        sa_select(SensorReading)
                        .where(
                            SensorReading.sensor_id.in_(sensor_ids),
                            SensorReading.recorded_at >= reading_cutoff,
                        )
                        .order_by(SensorReading.recorded_at.desc())
                        .limit(20)
                    )
                    for rdg in r_result.scalars().all():
                        if temp_val is None and rdg.temperature_c is not None:
                            temp_val = rdg.temperature_c
                        if hum_val is None and rdg.humidity is not None:
                            hum_val = rdg.humidity
                        if lux_val is None and rdg.lux is not None:
                            lux_val = rdg.lux
                        if occ_val is None and rdg.presence is not None:
                            occ_val = rdg.presence
                        if all(v is not None for v in (temp_val, hum_val, lux_val, occ_val)):
                            break

                # Read comfort preferences (frontend saves temp_min/temp_max)
                prefs = zone.comfort_preferences or {}
                temp_min = prefs.get("temp_min")
                temp_max = prefs.get("temp_max")
                if temp_min is not None and temp_max is not None:
                    comfort_str = f"{temp_min}-{temp_max}°C"
                else:
                    # Legacy fallback
                    legacy = prefs.get("target_temp") or prefs.get("ideal_temp")
                    comfort_str = f"{legacy}°C" if legacy else "not set"

                # Use multi-signal occupancy inference
                inferred_occ = await infer_zone_occupancy(str(zone.id), db)
                if inferred_occ is None:
                    # Fall back to raw presence sensor
                    inferred_occ = occ_val if occ_val is not None else False

                temp_str = f"{temp_val:.1f}°C" if temp_val is not None else "N/A"
                hum_str = f"{hum_val:.0f}%" if hum_val is not None else "N/A"
                lux_str = f"{lux_val:.0f} lx" if lux_val is not None else "N/A"
                occ_str = "occupied" if inferred_occ else "unoccupied"

                zone_summaries.append(
                    f"- {zone.name}: temp={temp_str}, humidity={hum_str}, "
                    f"lux={lux_str}, occupancy={occ_str}, comfort_range={comfort_str}"
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
                except Exception:  # noqa: S110
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
                "efficiency, current schedules, and ambient light levels.\n\n"
                "Lux context: High lux (>500 lx) near windows indicates direct sunlight "
                "and solar heat gain — consider lowering the target slightly in cooling "
                "mode. Low lux (<50 lx) in occupied zones may indicate evening/night — "
                "prioritize comfort. Unoccupied zones with low lux likely have no one "
                "home — favor energy savings.\n\n"
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
                f"Safety limits: {safety_min}°C - {safety_max}°C\n\n"
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
# Lux-Based Cover (Blind/Shade) Automation
# ============================================================================

# Default lux thresholds — overridden per-zone via comfort_preferences.lux_max
_DEFAULT_LUX_CLOSE = 500.0  # Close covers when lux exceeds this
_DEFAULT_LUX_OPEN = 200.0   # Re-open covers when lux drops below this
# Track last cover action per device to avoid flapping
_cover_last_action: dict[str, str] = {}  # device_id -> "open" | "closed"


async def execute_cover_automation() -> None:
    """Check lux levels in each zone and open/close blinds/shades accordingly.

    For each zone that has both a lux sensor reading and a blind/shade device:
    - If current lux > lux_max threshold -> close the cover (reduce solar gain)
    - If current lux < lux_open threshold -> open the cover (allow natural light)

    Uses hysteresis (separate open/close thresholds) to prevent flapping.
    """
    from datetime import timedelta as _td

    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import selectinload

    from backend.models.database import DeviceAction, SensorReading, Zone
    from backend.models.enums import ActionType, DeviceType, TriggerType

    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            zone_result = await db.execute(
                sa_select(Zone)
                .options(selectinload(Zone.sensors), selectinload(Zone.devices))
                .where(Zone.is_active.is_(True))
            )
            zones = zone_result.scalars().unique().all()

            ha_client = app_state.ha_client
            if ha_client is None:
                return

            reading_cutoff = datetime.now(UTC) - _td(minutes=15)

            for zone in zones:
                covers = [
                    d for d in (zone.devices or [])
                    if d.type in (DeviceType.blind.value, DeviceType.shade.value)
                    and d.ha_entity_id
                ]
                if not covers:
                    continue

                sensor_ids = [s.id for s in zone.sensors] if zone.sensors else []
                if not sensor_ids:
                    continue

                lux_result = await db.execute(
                    sa_select(SensorReading)
                    .where(
                        SensorReading.sensor_id.in_(sensor_ids),
                        SensorReading.recorded_at >= reading_cutoff,
                        SensorReading.lux.isnot(None),
                    )
                    .order_by(SensorReading.recorded_at.desc())
                    .limit(1)
                )
                lux_reading = lux_result.scalar_one_or_none()
                if lux_reading is None or lux_reading.lux is None:
                    continue

                current_lux = lux_reading.lux

                prefs = zone.comfort_preferences or {}
                lux_close = float(prefs.get("lux_max", _DEFAULT_LUX_CLOSE))
                lux_open_thresh = float(prefs.get("lux_open", _DEFAULT_LUX_OPEN))

                for cover in covers:
                    device_key = str(cover.id)
                    last_action = _cover_last_action.get(device_key)

                    if current_lux > lux_close and last_action != "closed":
                        try:
                            await ha_client.call_service(
                                "cover", "close_cover",
                                target={"entity_id": cover.ha_entity_id},
                            )
                            _cover_last_action[device_key] = "closed"
                            logger.info(
                                "Cover automation: closing %s (lux=%.0f > %.0f) in %s",
                                cover.ha_entity_id, current_lux, lux_close, zone.name,
                            )
                            db.add(DeviceAction(
                                device_id=cover.id,
                                zone_id=zone.id,
                                triggered_by=TriggerType.rule_engine,
                                action_type=ActionType.close_cover,
                                parameters={"lux": current_lux, "threshold": lux_close},
                                reasoning=f"Lux {current_lux:.0f} exceeded threshold {lux_close:.0f}",
                            ))
                        except Exception as exc:
                            logger.warning("Failed to close cover %s: %s", cover.ha_entity_id, exc)

                    elif current_lux < lux_open_thresh and last_action == "closed":
                        try:
                            await ha_client.call_service(
                                "cover", "open_cover",
                                target={"entity_id": cover.ha_entity_id},
                            )
                            _cover_last_action[device_key] = "open"
                            logger.info(
                                "Cover automation: opening %s (lux=%.0f < %.0f) in %s",
                                cover.ha_entity_id, current_lux, lux_open_thresh, zone.name,
                            )
                            db.add(DeviceAction(
                                device_id=cover.id,
                                zone_id=zone.id,
                                triggered_by=TriggerType.rule_engine,
                                action_type=ActionType.open_cover,
                                parameters={"lux": current_lux, "threshold": lux_open_thresh},
                                reasoning=f"Lux {current_lux:.0f} dropped below threshold {lux_open_thresh:.0f}",
                            ))
                        except Exception as exc:
                            logger.warning("Failed to open cover %s: %s", cover.ha_entity_id, exc)

                await db.commit()

    except Exception as e:
        logger.error("Error in cover automation: %s", e)


# ============================================================================
# Occupancy Inference (presence + lux + time-of-day + learned patterns)
# ============================================================================


async def infer_zone_occupancy(zone_id: str | uuid.UUID, db: object) -> bool | None:
    """Infer whether a zone is occupied using multi-signal fusion.

    Combines:
    1. Binary presence sensor (highest weight -- direct detection)
    2. Lux level (indirect -- lights on in evening suggests occupancy)
    3. Time-of-day patterns from PatternEngine (learned probability)

    Returns True (occupied), False (vacant), or None (insufficient data).
    """
    from datetime import timedelta as _td

    from sqlalchemy import select as sa_select

    from backend.models.database import OccupancyPattern, SensorReading
    from backend.models.database import Sensor as _Sensor
    from backend.models.enums import PatternType as _PT

    zone_uuid = uuid.UUID(str(zone_id)) if not isinstance(zone_id, uuid.UUID) else zone_id

    # Gather sensor IDs for this zone
    sensor_result = await db.execute(  # type: ignore[union-attr]
        sa_select(_Sensor.id).where(_Sensor.zone_id == zone_uuid)
    )
    sensor_ids = [row[0] for row in sensor_result.all()]
    if not sensor_ids:
        return None

    reading_cutoff = datetime.now(UTC) - _td(minutes=15)

    r_result = await db.execute(  # type: ignore[union-attr]
        sa_select(SensorReading)
        .where(
            SensorReading.sensor_id.in_(sensor_ids),
            SensorReading.recorded_at >= reading_cutoff,
        )
        .order_by(SensorReading.recorded_at.desc())
        .limit(20)
    )
    readings = r_result.scalars().all()

    # Extract latest values
    presence: bool | None = None
    lux: float | None = None
    for rdg in readings:
        if presence is None and rdg.presence is not None:
            presence = rdg.presence
        if lux is None and rdg.lux is not None:
            lux = rdg.lux
        if presence is not None and lux is not None:
            break

    # -- Signal 1: Direct presence sensor (weight: 0.6) --
    presence_score: float | None = None
    if presence is not None:
        presence_score = 1.0 if presence else 0.0

    # -- Signal 2: Lux-based inference (weight: 0.2) --
    lux_score: float | None = None
    if lux is not None:
        now = datetime.now(UTC)
        hour = now.hour
        is_evening_night = hour >= 18 or hour < 6
        if is_evening_night:
            # In evening/night, high lux (lights on) suggests occupancy
            if lux > 100:
                lux_score = 0.8
            elif lux > 30:
                lux_score = 0.4
            else:
                lux_score = 0.1  # Dark room at night -- likely empty
        else:
            # During daytime, lux is less informative (could be sunlight)
            lux_score = 0.5  # Neutral

    # -- Signal 3: Learned pattern probability (weight: 0.2) --
    pattern_score: float | None = None
    try:
        now = datetime.now(UTC)
        day_str = now.strftime("%a").lower()
        slot = now.hour * 12 + now.minute // 5
        key = f"{day_str}:{slot}"

        pattern_result = await db.execute(  # type: ignore[union-attr]
            sa_select(OccupancyPattern)
            .where(
                OccupancyPattern.zone_id == zone_uuid,
                OccupancyPattern.pattern_type == _PT.weekday,
            )
            .order_by(OccupancyPattern.created_at.desc())
            .limit(1)
        )
        pattern = pattern_result.scalar_one_or_none()
        if pattern and pattern.schedule:
            for entry in pattern.schedule:
                if entry.get("bucket") == key:
                    pattern_score = entry.get("probability", 0.0)
                    break
    except Exception:
        logger.debug("Could not load occupancy pattern for zone %s", zone_id, exc_info=True)

    # -- Weighted fusion --
    total_weight = 0.0
    weighted_sum = 0.0

    if presence_score is not None:
        weighted_sum += 0.6 * presence_score
        total_weight += 0.6
    if lux_score is not None:
        weighted_sum += 0.2 * lux_score
        total_weight += 0.2
    if pattern_score is not None:
        weighted_sum += 0.2 * pattern_score
        total_weight += 0.2

    if total_weight == 0:
        return None

    probability = weighted_sum / total_weight
    return probability >= 0.5


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

    # Validate sensor values are within physically plausible ranges.
    # Null out bad fields instead of discarding the entire reading so that
    # other valid values (e.g. humidity, presence) are still persisted.
    if change.temperature is not None and (change.temperature < -40 or change.temperature > 60):
        logger.warning(
            "Dropping impossible temperature %.1f°C from %s (keeping other fields)",
            change.temperature,
            change.entity_id,
        )
        change.temperature = None
    if change.humidity is not None and (change.humidity < 0 or change.humidity > 100):
        logger.warning(
            "Dropping impossible humidity %.1f%% from %s (keeping other fields)",
            change.humidity,
            change.entity_id,
        )
        change.humidity = None

    has_useful_value = (
        change.temperature is not None
        or change.humidity is not None
        or change.lux is not None
        or change.presence is not None
    )

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
                return

            # Always update last_seen so the sensor doesn't appear offline.
            # Many Zigbee2MQTT entities report battery/linkquality/voltage
            # that don't parse into temp/humidity/lux/presence — but the
            # sensor IS alive and reporting.
            sensor.last_seen = change.timestamp

            if not has_useful_value:
                # No climate data to persist, but we still updated last_seen
                await db.commit()
                return

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

    # Lux-based cover (blind/shade) automation - every 3 minutes
    scheduler.add_job(
        execute_cover_automation,
        IntervalTrigger(minutes=3),
        id="execute_cover_automation",
        name="Lux Cover Automation",
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
                # Build entity filter from config, DB settings, AND registered sensors.
                entity_filter: set[str] | None = None

                # 1. Config-file entities
                _climate = settings.climate_entities.strip()
                _sensors = settings.sensor_entities.strip()
                _config_entities: set[str] = set()
                if _climate:
                    _config_entities.update(e.strip() for e in _climate.split(",") if e.strip())
                if _sensors:
                    _config_entities.update(e.strip() for e in _sensors.split(",") if e.strip())

                # 2. DB system_settings KV overrides (user may change via Settings UI)
                _db_entities: set[str] = set()
                try:
                    from backend.models.database import Sensor as SensorModel
                    from backend.models.database import SystemSetting
                    _SessionMaker = get_session_maker()
                    async with _SessionMaker() as _sess:
                        from sqlalchemy import select as _sel
                        for _key in ("climate_entities", "sensor_entities"):
                            _row = (await _sess.execute(
                                _sel(SystemSetting).where(SystemSetting.key == _key)
                            )).scalar_one_or_none()
                            if _row and _row.value:
                                _raw = _row.value.get("value", "")
                                if isinstance(_raw, str) and _raw.strip():
                                    _db_entities.update(
                                        e.strip() for e in _raw.split(",") if e.strip()
                                    )

                        # 3. All registered sensors with ha_entity_id
                        _sensor_rows = (await _sess.execute(
                            _sel(SensorModel.ha_entity_id).where(
                                SensorModel.ha_entity_id.isnot(None),
                                SensorModel.ha_entity_id != "",
                            )
                        )).scalars().all()
                        _db_entities.update(eid for eid in _sensor_rows if eid)
                except Exception as _db_err:
                    logger.warning("Could not read DB entities for WS filter: %s", _db_err)

                _all_entities = _config_entities | _db_entities
                if _all_entities:
                    entity_filter = _all_entities
                    logger.info(
                        "Entity filter active: %d entities (%d config, %d DB)",
                        len(entity_filter),
                        len(_config_entities),
                        len(_db_entities),
                    )

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
                    import backend.api.dependencies as _deps
                    from backend.integrations import HAClient as _HAClient
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
