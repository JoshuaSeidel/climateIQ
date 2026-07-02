"""Pattern learning engine for ClimateIQ."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import OccupancyPattern, PatternType, Season

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OccupancyReading:
    zone_id: str
    timestamp: datetime
    occupied: bool


@dataclass(slots=True)
class ThermalReading:
    zone_id: str
    timestamp: datetime
    temperature_c: float
    hvac_output: float | None = None


class PatternEngine:
    """Learn occupancy and thermal profiles for each zone."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._occupancy_cache: dict[str, dict[str, float]] = {}
        self._thermal_cache: dict[str, dict[str, float]] = {}
        self._preconditioning: dict[str, int] = {}

    async def learn_occupancy_patterns(
        self, zone_id: str, readings: Iterable[OccupancyReading]
    ) -> dict[str, float]:
        buckets: dict[str, list[int]] = defaultdict(list)
        now = datetime.now(UTC)
        for reading in readings:
            if reading.zone_id != zone_id:
                continue
            if now - reading.timestamp > timedelta(days=30):
                continue
            day = reading.timestamp.strftime("%a").lower()
            slot = reading.timestamp.hour * 12 + reading.timestamp.minute // 5
            key = f"{day}:{slot}"
            buckets[key].append(1 if reading.occupied else 0)

        probabilities: dict[str, float] = {}
        for key, samples in buckets.items():
            probabilities[key] = round(mean(samples), 3)

        self._occupancy_cache[zone_id] = probabilities
        try:
            await self._persist_pattern(zone_id, PatternType.weekday, probabilities)
        except Exception:
            logger.warning("Skipping occupancy pattern persistence", exc_info=True)
        return probabilities

    async def learn_thermal_profile(
        self, zone_id: str, readings: Iterable[ThermalReading]
    ) -> dict[str, float]:
        deltas: list[float] = []
        last_temp: float | None = None
        last_ts: datetime | None = None
        for reading in sorted(
            (r for r in readings if r.zone_id == zone_id), key=lambda r: r.timestamp
        ):
            if last_temp is not None and last_ts is not None:
                dt = (reading.timestamp - last_ts).total_seconds() / 60
                if dt >= 1:
                    deltas.append((reading.temperature_c - last_temp) / dt)
            last_temp = reading.temperature_c
            last_ts = reading.timestamp

        if not deltas:
            profile = {"avg_trend_c_per_min": 0.0}
        else:
            profile = {
                "avg_trend_c_per_min": round(mean(deltas), 4),
                "max_delta_c_per_min": round(max(deltas), 4),
            }

        self._thermal_cache[zone_id] = profile
        heat_capacity = 0.0
        if deltas:
            heating = [d for d in deltas if d > 0]
            cooling = [abs(d) for d in deltas if d < 0]
            if heating:
                heat_capacity = round(1.0 / (mean(heating) or 0.001), 2)
            else:
                heat_capacity = 0.0
            profile["cooling_rate_c_per_min"] = round(mean(cooling), 4) if cooling else 0.0
        profile["heat_capacity_minutes_per_c"] = heat_capacity
        return profile

    def predict_occupancy(self, zone_id: str, day: str, time_of_day: datetime) -> float:
        cache = self._occupancy_cache.get(zone_id)
        if not cache:
            return 0.0
        slot = time_of_day.hour * 12 + time_of_day.minute // 5
        key = f"{day.lower()}:{slot}"
        return cache.get(key, 0.0)

    def get_preconditioning_time(
        self,
        zone_id: str,
        *,
        current_temp_c: float | None = None,
        target_temp_c: float | None = None,
        outdoor_temp_c: float | None = None,
        hvac_mode: str | None = None,
        thermal_profile: dict[str, float] | None = None,
    ) -> int:
        """Return the number of minutes to start conditioning before a schedule fires.

        When only ``zone_id`` is provided, falls back to a simple lead-time cache
        keyed by the zone's average temperature trend. Passing the current/target
        temps and (optionally) outdoor temp + a thermal profile enables a
        weather-aware calculation: the further the room is from target — and the
        more hostile outdoor conditions are — the earlier we start.
        """
        cache = self._thermal_cache.get(zone_id)

        # Weather-aware branch: we have enough data to model lead time explicitly.
        if (
            current_temp_c is not None
            and target_temp_c is not None
            and hvac_mode in ("heat", "cool")
        ):
            gap_c = abs(target_temp_c - current_temp_c)
            if gap_c < 0.3:
                return 0  # already at target, no lead-in needed

            # Preferred rate source: thermal_profile from zone_analytics
            # (heating_rate_c_per_hour / cooling_rate_c_per_hour). Fall back to
            # the pattern-engine's own trend cache if analytics haven't populated
            # yet.
            profile = thermal_profile or {}
            if hvac_mode == "heat":
                rate_c_per_min = abs(profile.get("heating_rate_c_per_hour", 0.0)) / 60.0
            else:
                rate_c_per_min = abs(profile.get("cooling_rate_c_per_hour", 0.0)) / 60.0
            if rate_c_per_min < 1e-4 and cache:
                rate_c_per_min = abs(cache.get("avg_trend_c_per_min", 0.0)) or 0.0
            if rate_c_per_min < 1e-4:
                rate_c_per_min = 0.05  # conservative default: ~3°C/hour

            base_minutes = gap_c / rate_c_per_min

            # Outdoor difficulty bump: HVAC works harder against a hostile
            # outdoors. For heat, cold outdoors slows us; for cool, hot outdoors
            # slows us. ~0.5 min per °C of adverse outdoor delta beyond a 3°C
            # tolerance band.
            outdoor_bump = 0.0
            if outdoor_temp_c is not None:
                if hvac_mode == "heat":
                    delta = target_temp_c - outdoor_temp_c
                else:
                    delta = outdoor_temp_c - target_temp_c
                outdoor_bump = max(0.0, delta - 3.0) * 0.5

            minutes = int(max(5, min(120, round(base_minutes + outdoor_bump))))
            return minutes

        # Legacy branch — original static formula (unchanged behaviour).
        if not cache:
            return 0
        if zone_id in self._preconditioning:
            return self._preconditioning[zone_id]
        rate = cache.get("avg_trend_c_per_min") or 0.05
        if rate <= 0:
            rate = 0.05
        minutes = int(max(5, min(120, 1.5 / rate)))
        self._preconditioning[zone_id] = minutes
        return minutes

    async def _persist_pattern(
        self, zone_id: str, pattern_type: PatternType, data: dict[str, float]
    ) -> None:
        import uuid as _uuid

        zone_uuid = _uuid.UUID(zone_id) if not isinstance(zone_id, _uuid.UUID) else zone_id

        stmt = select(OccupancyPattern).where(
            OccupancyPattern.zone_id == zone_uuid,
            OccupancyPattern.pattern_type == pattern_type,
            OccupancyPattern.season == _current_season(),
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        payload = [{"bucket": k, "probability": v} for k, v in sorted(data.items())]
        confidence = sum(v for v in data.values()) / max(len(data), 1)
        if existing:
            await self._session.execute(
                update(OccupancyPattern)
                .where(OccupancyPattern.id == existing.id)
                .values(schedule=payload, confidence=confidence)
            )
        else:
            await self._session.execute(
                insert(OccupancyPattern).values(
                    zone_id=zone_uuid,
                    pattern_type=pattern_type,
                    season=_current_season(),
                    schedule=payload,
                    confidence=confidence,
                )
            )
        await self._session.commit()


def _current_season() -> Season:
    month = datetime.now(UTC).month
    if month in (12, 1, 2):
        return Season.winter
    if month in (3, 4, 5):
        return Season.spring
    if month in (6, 7, 8):
        return Season.summer
    return Season.fall


__all__ = ["OccupancyReading", "PatternEngine", "ThermalReading"]
