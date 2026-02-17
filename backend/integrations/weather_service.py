"""Weather service for ClimateIQ — fetches and caches weather data from Home Assistant.

Parses the HA ``weather.*`` entity format (current conditions + forecast)
and exposes typed dataclasses for the rest of the application.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .ha_client import HAClient, HAClientError, HANotFoundError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WeatherData:
    """Current weather conditions from a Home Assistant weather entity."""

    state: str  # e.g. "sunny", "cloudy", "rainy"
    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    visibility: float | None = None
    ozone: float | None = None
    temperature_unit: str = "°C"
    pressure_unit: str = "hPa"
    wind_speed_unit: str = "km/h"
    visibility_unit: str = "km"
    attribution: str = ""
    entity_id: str = ""
    last_updated: str = ""


@dataclass(slots=True)
class ForecastEntry:
    """A single forecast time-slot from the HA weather entity."""

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


# ---------------------------------------------------------------------------
# Internal cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Timestamped cache wrapper."""

    data: Any = None
    timestamp: float = 0.0

    def is_valid(self, ttl: int) -> bool:
        return self.data is not None and (time.monotonic() - self.timestamp) < ttl


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

_DEFAULT_WEATHER_ENTITY = ""


class WeatherService:
    """Fetch and cache weather data from Home Assistant.

    Usage::

        service = WeatherService(ha_client, weather_entity="weather.home")
        current = await service.get_current()
        forecast = await service.get_forecast(hours=12)
    """

    def __init__(
        self,
        ha_client: HAClient,
        *,
        cache_ttl: int = 300,
        weather_entity: str = _DEFAULT_WEATHER_ENTITY,
    ) -> None:
        if not weather_entity:
            raise ValueError("weather_entity must be specified — configure it in Settings")
        self._ha = ha_client
        self._cache_ttl = cache_ttl
        self._weather_entity = weather_entity

        # Separate caches for current conditions and forecast
        self._current_cache = _CacheEntry()
        self._forecast_cache = _CacheEntry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_current(self) -> WeatherData:
        """Return current weather conditions.

        Results are cached for ``cache_ttl`` seconds.  If the HA entity
        cannot be reached the last cached value is returned (if any),
        otherwise the error propagates.
        """
        if self._current_cache.is_valid(self._cache_ttl):
            logger.debug("Returning cached current weather")
            cached = self._current_cache.data
            if isinstance(cached, WeatherData):
                return cached

        try:
            entity = await self._ha.get_state(self._weather_entity)
        except HANotFoundError:
            logger.error(
                "Weather entity %s not found in Home Assistant",
                self._weather_entity,
            )
            cached = self._current_cache.data
            if isinstance(cached, WeatherData):
                logger.warning("Returning stale cached weather data")
                return cached
            raise
        except HAClientError:
            logger.exception("Failed to fetch weather from Home Assistant")
            cached = self._current_cache.data
            if isinstance(cached, WeatherData):
                logger.warning("Returning stale cached weather data")
                return cached
            raise

        weather = self._parse_current(entity.entity_id, entity.state, entity.attributes)
        weather.last_updated = entity.last_updated

        self._current_cache = _CacheEntry(data=weather, timestamp=time.monotonic())
        logger.info(
            "Fetched current weather: %s, %.1f%s",
            weather.state,
            weather.temperature if weather.temperature is not None else 0,
            weather.temperature_unit,
        )
        return weather

    async def get_forecast(self, hours: int = 24) -> list[ForecastEntry]:
        """Return hourly forecast entries for the next *hours* hours.

        Home Assistant weather entities expose forecast data in their
        attributes.  The number of entries returned depends on the
        integration providing the weather entity.

        Args:
            hours: Maximum number of hours of forecast to return.
                   Each entry typically represents one hour.

        Returns:
            List of ``ForecastEntry`` objects, possibly shorter than
            *hours* if the source provides fewer data points.
        """
        if self._forecast_cache.is_valid(self._cache_ttl):
            cached = self._forecast_cache.data
            if isinstance(cached, list):
                logger.debug("Returning cached forecast (%d entries)", len(cached))
                return [entry for entry in cached if isinstance(entry, ForecastEntry)][:hours]

        try:
            entity = await self._ha.get_state(self._weather_entity)
        except HANotFoundError:
            logger.error(
                "Weather entity %s not found in Home Assistant",
                self._weather_entity,
            )
            cached = self._forecast_cache.data
            if isinstance(cached, list):
                logger.warning("Returning stale cached forecast")
                return [entry for entry in cached if isinstance(entry, ForecastEntry)][:hours]
            raise
        except HAClientError:
            logger.exception("Failed to fetch forecast from Home Assistant")
            cached = self._forecast_cache.data
            if isinstance(cached, list):
                logger.warning("Returning stale cached forecast")
                return [entry for entry in cached if isinstance(entry, ForecastEntry)][:hours]
            raise

        raw_forecast = entity.attributes.get("forecast", [])
        entries = [
            self._parse_forecast_entry(item) for item in raw_forecast if isinstance(item, dict)
        ]

        self._forecast_cache = _CacheEntry(data=entries, timestamp=time.monotonic())
        logger.info("Fetched forecast with %d entries", len(entries))
        return entries[:hours]

    def invalidate_cache(self) -> None:
        """Force-expire both caches so the next call fetches fresh data."""
        self._current_cache = _CacheEntry()
        self._forecast_cache = _CacheEntry()
        logger.debug("Weather cache invalidated")

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_current(
        entity_id: str,
        state: str,
        attrs: dict[str, Any],
    ) -> WeatherData:
        """Build a ``WeatherData`` from a HA weather entity's state + attributes."""
        return WeatherData(
            entity_id=entity_id,
            state=state,
            temperature=_safe_float(attrs.get("temperature")),
            humidity=_safe_float(attrs.get("humidity")),
            pressure=_safe_float(attrs.get("pressure")),
            wind_speed=_safe_float(attrs.get("wind_speed")),
            wind_bearing=_safe_float(attrs.get("wind_bearing")),
            visibility=_safe_float(attrs.get("visibility")),
            ozone=_safe_float(attrs.get("ozone")),
            temperature_unit=attrs.get("temperature_unit", "°C"),
            pressure_unit=attrs.get("pressure_unit", "hPa"),
            wind_speed_unit=attrs.get("wind_speed_unit", "km/h"),
            visibility_unit=attrs.get("visibility_unit", "km"),
            attribution=attrs.get("attribution", ""),
        )

    @staticmethod
    def _parse_forecast_entry(item: dict[str, Any]) -> ForecastEntry:
        """Parse a single forecast dict from the HA weather entity attributes."""
        return ForecastEntry(
            datetime=item.get("datetime", ""),
            temperature=_safe_float(item.get("temperature")),
            templow=_safe_float(item.get("templow")),
            humidity=_safe_float(item.get("humidity")),
            condition=item.get("condition", ""),
            precipitation=_safe_float(item.get("precipitation")),
            precipitation_probability=_safe_float(item.get("precipitation_probability")),
            wind_speed=_safe_float(item.get("wind_speed")),
            wind_bearing=_safe_float(item.get("wind_bearing")),
            is_daytime=item.get("is_daytime"),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    """Coerce *value* to float, returning ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ForecastEntry",
    "WeatherData",
    "WeatherService",
]
