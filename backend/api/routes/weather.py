"""Weather API routes for ClimateIQ HVAC system.

Provides current weather conditions and forecast data sourced from
Home Assistant via ``WeatherService``, with Redis caching and staleness
tracking.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db, get_ha_client, get_redis
from backend.integrations import HAClient, WeatherService
from backend.integrations.ha_client import HAClientError
from backend.integrations.weather_service import WeatherData
from backend.models.database import SystemSetting

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CURRENT_CACHE_KEY = "weather:current"
_FORECAST_CACHE_KEY = "weather:forecast"

_CURRENT_FRESH_TTL = 300  # 5 minutes — data considered fresh
_FORECAST_FRESH_TTL = 900  # 15 minutes — forecast considered fresh
_STALE_LIMIT = 3600  # 1 hour — beyond this, discard cache entirely
_REDIS_TTL = 3600  # hard Redis key expiry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_weather_entity(db: AsyncSession) -> str:
    """Read the configured weather entity from the key-value table.

    Returns an empty string if not configured.
    """
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "weather_entity"))
    row = result.scalar_one_or_none()
    if row is None:
        return ""
    value: str = row.value.get("value", "")
    return value


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class WeatherEntityInfo(BaseModel):
    """A weather entity available in Home Assistant."""

    entity_id: str
    name: str
    state: str


class WeatherDataResponse(BaseModel):
    state: str
    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    visibility: float | None = None
    temperature_unit: str = "°C"
    pressure_unit: str = "hPa"
    wind_speed_unit: str = "km/h"
    visibility_unit: str = "km"
    attribution: str = ""
    entity_id: str = ""
    last_updated: str = ""


class ForecastEntryResponse(BaseModel):
    datetime: str = ""
    temperature: float | None = None
    templow: float | None = None
    humidity: float | None = None
    condition: str = ""
    precipitation: float | None = None
    precipitation_probability: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    is_daytime: bool | None = None


class WeatherEnvelope(BaseModel):
    source: str
    cached: bool
    stale: bool
    cache_age_seconds: float | None = None
    fetched_at: str
    data: WeatherDataResponse


class ForecastEnvelope(BaseModel):
    source: str
    cached: bool
    stale: bool
    cache_age_seconds: float | None = None
    fetched_at: str
    data: list[ForecastEntryResponse]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _weather_data_to_dict(wd: WeatherData) -> dict[str, Any]:
    """Convert a WeatherData dataclass to a JSON-safe dict.

    Drops the ``ozone`` field since it is not exposed in the response model.
    """
    d: dict[str, Any] = asdict(wd)
    d.pop("ozone", None)
    return d


def _try_parse_cached(raw: str | None) -> dict[str, Any] | None:
    """Attempt to parse a Redis-cached JSON string.

    Returns ``None`` if the value is missing, not valid JSON, or does not
    contain the expected ``fetched_at`` / ``data`` structure.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Redis cache contained non-JSON data; discarding")
        return None
    if not isinstance(parsed, dict) or "fetched_at" not in parsed or "data" not in parsed:
        logger.warning("Redis cache missing expected structure; discarding")
        return None
    result: dict[str, Any] = parsed
    return result


def _cache_age(fetched_at_iso: str) -> float:
    """Return seconds elapsed since *fetched_at_iso* (UTC ISO-8601)."""
    try:
        fetched_dt = datetime.fromisoformat(fetched_at_iso)
        # Ensure timezone-aware comparison
        if fetched_dt.tzinfo is None:
            fetched_dt = fetched_dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - fetched_dt).total_seconds()
    except (ValueError, TypeError):
        # Unparseable timestamp — treat as expired
        return _STALE_LIMIT + 1


async def _redis_get(redis_client: aioredis.Redis, key: str) -> str | None:
    """Read a key from Redis, returning ``None`` on any failure."""
    try:
        result: Any = await redis_client.get(key)
        if isinstance(result, bytes):
            return result.decode()
        if isinstance(result, str):
            return result
        return None
    except Exception:
        logger.warning("Redis unavailable for GET %s; skipping cache", key)
        return None


async def _redis_setex(redis_client: aioredis.Redis, key: str, ttl: int, value: str) -> None:
    """Write a key to Redis with TTL, silently ignoring failures."""
    try:
        await redis_client.setex(key, ttl, value)
    except Exception:
        logger.warning("Redis unavailable for SETEX %s; cache not updated", key)


# ---------------------------------------------------------------------------
# GET /weather/entities — list available weather entities from HA
# ---------------------------------------------------------------------------


@router.get("/entities", response_model=list[WeatherEntityInfo])
async def list_weather_entities(
    ha_client: Annotated[HAClient, Depends(get_ha_client)],
) -> list[WeatherEntityInfo]:
    """Return all ``weather.*`` entities known to Home Assistant.

    Used by the frontend Settings page to let the user pick which
    weather entity to use for forecasts and current conditions.
    """
    try:
        states = await ha_client.get_states()
    except HAClientError as exc:
        logger.error("Failed to fetch HA states: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to reach Home Assistant",
        ) from exc

    entities: list[WeatherEntityInfo] = []
    for entity in states:
        if entity.entity_id.startswith("weather."):
            entities.append(
                WeatherEntityInfo(
                    entity_id=entity.entity_id,
                    name=entity.attributes.get("friendly_name", entity.entity_id),
                    state=entity.state,
                )
            )
    return entities


# ---------------------------------------------------------------------------
# GET /weather/current
# ---------------------------------------------------------------------------


@router.get("/current", response_model=WeatherEnvelope)
async def get_current_weather(
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
    ha_client: Annotated[HAClient, Depends(get_ha_client)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WeatherEnvelope:
    """Return current weather conditions with cache metadata.

    Checks Redis first; falls back to a live fetch from Home Assistant
    if the cache is missing or expired (> 1 hour).
    """
    weather_entity = await _get_weather_entity(db)
    if not weather_entity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No weather entity configured. Go to Settings to select one.",
        )

    # --- 1. Try Redis cache ---
    raw = await _redis_get(redis_client, _CURRENT_CACHE_KEY)
    cached_record = _try_parse_cached(raw)

    if cached_record is not None:
        age = _cache_age(cached_record["fetched_at"])

        if age <= _STALE_LIMIT:
            stale = age > _CURRENT_FRESH_TTL
            return WeatherEnvelope(
                source="cache",
                cached=True,
                stale=stale,
                cache_age_seconds=round(age, 1),
                fetched_at=cached_record["fetched_at"],
                data=WeatherDataResponse(**cached_record["data"]),
            )
        # age > _STALE_LIMIT → expired, fall through to live fetch
        logger.info("Cached current weather expired (age=%.0fs); fetching live", age)

    # --- 2. Live fetch ---
    try:
        service = WeatherService(ha_client, weather_entity=weather_entity)
        weather = await service.get_current()
    except HAClientError as exc:
        logger.error("Live weather fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather data unavailable",
        ) from exc

    fetched_at = datetime.now(UTC).isoformat()
    data_dict = _weather_data_to_dict(weather)

    # --- 3. Store in Redis as proper JSON ---
    cache_payload = json.dumps({"fetched_at": fetched_at, "data": data_dict})
    await _redis_setex(redis_client, _CURRENT_CACHE_KEY, _REDIS_TTL, cache_payload)

    return WeatherEnvelope(
        source="live",
        cached=False,
        stale=False,
        cache_age_seconds=None,
        fetched_at=fetched_at,
        data=WeatherDataResponse(**data_dict),
    )


# ---------------------------------------------------------------------------
# GET /weather/forecast
# ---------------------------------------------------------------------------


@router.get("/forecast", response_model=ForecastEnvelope)
async def get_weather_forecast(
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
    ha_client: Annotated[HAClient, Depends(get_ha_client)],
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> ForecastEnvelope:
    """Return hourly weather forecast with cache metadata.

    Checks Redis first; falls back to a live fetch from Home Assistant
    if the cache is missing or expired (> 1 hour).
    """
    weather_entity = await _get_weather_entity(db)
    if not weather_entity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No weather entity configured. Go to Settings to select one.",
        )

    # --- 1. Try Redis cache ---
    raw = await _redis_get(redis_client, _FORECAST_CACHE_KEY)
    cached_record = _try_parse_cached(raw)

    if cached_record is not None:
        age = _cache_age(cached_record["fetched_at"])

        if age <= _STALE_LIMIT:
            stale = age > _FORECAST_FRESH_TTL
            entries = cached_record["data"]
            if isinstance(entries, list):
                return ForecastEnvelope(
                    source="cache",
                    cached=True,
                    stale=stale,
                    cache_age_seconds=round(age, 1),
                    fetched_at=cached_record["fetched_at"],
                    data=[ForecastEntryResponse(**e) for e in entries[:hours]],
                )
        logger.info("Cached forecast expired (age=%.0fs); fetching live", age)

    # --- 2. Live fetch ---
    try:
        service = WeatherService(ha_client, weather_entity=weather_entity)
        forecast = await service.get_forecast(hours=hours)
    except HAClientError as exc:
        logger.error("Live forecast fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"detail": "Weather data unavailable", "source": "none"},
        ) from exc

    fetched_at = datetime.now(UTC).isoformat()
    data_list = [asdict(entry) for entry in forecast]

    # --- 3. Store in Redis as proper JSON ---
    cache_payload = json.dumps({"fetched_at": fetched_at, "data": data_list})
    await _redis_setex(redis_client, _FORECAST_CACHE_KEY, _REDIS_TTL, cache_payload)

    return ForecastEnvelope(
        source="live",
        cached=False,
        stale=False,
        cache_age_seconds=None,
        fetched_at=fetched_at,
        data=[ForecastEntryResponse(**e) for e in data_list[:hours]],
    )
