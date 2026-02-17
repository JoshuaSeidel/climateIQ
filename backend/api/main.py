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

            if stale_sensors:
                logger.info("Sensor health check: %d sensors offline", len(stale_sensors))
    except Exception as e:
        logger.error(f"Error checking sensor health: {e}")


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
        logger.info("Initializing database connection...")
        await init_db()

        # Initialize Redis and share with dependencies
        logger.info("Connecting to Redis...")
        app_state.redis_client = await init_redis()
        from backend.api.dependencies import set_shared_redis

        set_shared_redis(app_state.redis_client)

        await app_state.ws_manager.subscribe_redis()

        # Connect to MQTT only if a broker is explicitly configured
        if settings_instance.mqtt_broker:
            logger.info("Connecting to MQTT broker at %s...", settings_instance.mqtt_broker)
            try:
                await app_state.ws_manager.connect_mqtt(
                    broker=settings_instance.mqtt_broker,
                    port=settings_instance.mqtt_port,
                    username=settings_instance.mqtt_username or None,
                    password=settings_instance.mqtt_password or None,
                    use_tls=settings_instance.mqtt_use_tls,
                )
            except Exception as e:
                logger.warning("MQTT connection failed (non-fatal): %s", e)
        else:
            logger.info("No MQTT broker configured — skipping MQTT connection")

        # Initialize and start scheduler
        logger.info("Starting background scheduler...")
        app_state.scheduler = init_scheduler()
        app_state.scheduler.start()

        # Connect to Home Assistant WebSocket for real-time sensor data
        settings = settings_instance
        if settings.home_assistant_token:
            logger.info("Connecting to Home Assistant WebSocket...")
            try:
                ha_ws = HAWebSocketClient(
                    url=str(settings.home_assistant_url),
                    token=settings.home_assistant_token,
                )
                ha_ws.add_callback(_handle_ha_state_change)
                await ha_ws.connect()
                app_state.ha_ws = ha_ws
            except Exception as e:
                logger.warning(f"HA WebSocket connection failed (sensor ingestion degraded): {e}")

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
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
        access_log=True,
    )
