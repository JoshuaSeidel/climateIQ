"""Zone thermal and occupancy analytics.

This module runs as a background task every 4 hours. It analyzes sensor
readings and device actions stored in TimescaleDB to build a per-zone
thermal profile: how fast each room heats and cools, how it responds to
setpoint changes, when it is typically occupied, and whether sleep or nap
patterns have been detected via lux + presence.

The computed profile is persisted to ``zones.thermal_profile`` (JSONB) so
the LLM climate advisor can use it for predictive decisions without re-
running expensive queries on every maintenance tick.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ANALYSIS_DAYS = 30           # how many days of sensor_readings to use
_MIN_READINGS_FOR_RATE = 12   # minimum readings before we trust a rate calc
_NAP_HOUR_START = 11          # midday window start (11 AM)
_NAP_HOUR_END = 16            # midday window end (4 PM)
_SLEEP_LUX_THRESHOLD = 10.0   # lux <= this → likely dark / sleeping
_OCC_PRESENCE_WEIGHT = 0.7    # weight for a presence=True reading in score
_OCC_LUX_WEIGHT = 0.3         # weight for lux > threshold in score


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _linear_rate(times_h: list[float], temps_c: list[float]) -> float | None:
    """Compute °C/hour via simple linear regression. Returns None if not enough data."""
    n = len(times_h)
    if n < 2:
        return None
    mean_t = sum(times_h) / n
    mean_T = sum(temps_c) / n
    num = sum((times_h[i] - mean_t) * (temps_c[i] - mean_T) for i in range(n))
    den = sum((times_h[i] - mean_t) ** 2 for i in range(n))
    if abs(den) < 1e-9:
        return None
    return num / den


def _safe_mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# Main analytics class
# ---------------------------------------------------------------------------

class ZoneAnalytics:
    """Computes and persists thermal + occupancy profiles for zones."""

    def __init__(self, db: Any, ha_client: Any | None = None) -> None:
        self._db = db
        self._ha_client = ha_client

    async def run_all(self) -> None:
        """Analyze all active zones and persist updated profiles."""
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import selectinload

        from backend.models.database import Zone

        result = await self._db.execute(
            sa_select(Zone)
            .options(selectinload(Zone.sensors))
            .where(Zone.is_active.is_(True))
        )
        zones: list[Any] = list(result.scalars().unique().all())
        logger.info("ZoneAnalytics: analyzing %d active zones", len(zones))
        for zone in zones:
            try:
                await self.analyze_zone(zone)
            except Exception as exc:
                logger.warning("ZoneAnalytics: failed for zone '%s': %s", zone.name, exc)

    async def analyze_zone(self, zone: Any) -> None:
        """Analyze a single zone and write results to zone.thermal_profile."""
        from sqlalchemy import update as sa_update

        from backend.models.database import Zone

        profile = await self._compute_profile(zone)
        if not profile:
            return

        await self._db.execute(
            sa_update(Zone)
            .where(Zone.id == zone.id)
            .values(thermal_profile=profile)
        )
        await self._db.commit()
        logger.info(
            "ZoneAnalytics: updated profile for '%s' "
            "(heat=%.2f°C/h, cool=%.2f°C/h, lag=%.0f min, data_days=%d)",
            zone.name,
            profile.get("heating_rate_c_per_hour", 0),
            profile.get("cooling_rate_c_per_hour", 0),
            profile.get("response_lag_minutes", 0),
            profile.get("data_days", 0),
        )

    # ------------------------------------------------------------------
    # Profile computation
    # ------------------------------------------------------------------

    async def _compute_profile(self, zone: Any) -> dict[str, Any] | None:
        """Compute the full thermal + occupancy profile for one zone."""
        sensor_ids = [s.id for s in zone.sensors if s.id]
        if not sensor_ids:
            return None

        cutoff = datetime.now(UTC) - timedelta(days=_ANALYSIS_DAYS)
        readings = await self._load_sensor_readings(sensor_ids, cutoff)
        if len(readings) < _MIN_READINGS_FOR_RATE:
            logger.debug(
                "ZoneAnalytics: not enough readings for '%s' (%d < %d)",
                zone.name, len(readings), _MIN_READINGS_FOR_RATE,
            )
            return None

        actions = await self._load_device_actions(zone.id, cutoff)
        data_days = min(
            _ANALYSIS_DAYS,
            max(1, int((datetime.now(UTC) - readings[0]["recorded_at"]).days)),
        ) if readings else _ANALYSIS_DAYS

        thermal = self._compute_thermal_rates(readings, actions)
        occupancy = self._compute_occupancy(readings)

        return {
            **thermal,
            **occupancy,
            "data_days": data_days,
            "last_analyzed": datetime.now(UTC).isoformat(),
        }

    async def _load_sensor_readings(
        self,
        sensor_ids: list[Any],
        cutoff: datetime,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import select as sa_select

        from backend.models.database import SensorReading

        result = await self._db.execute(
            sa_select(
                SensorReading.recorded_at,
                SensorReading.temperature_c,
                SensorReading.presence,
                SensorReading.lux,
            )
            .where(
                SensorReading.sensor_id.in_(sensor_ids),
                SensorReading.temperature_c.is_not(None),
                SensorReading.recorded_at >= cutoff,
            )
            .order_by(SensorReading.recorded_at.asc())
        )
        return [
            {
                "recorded_at": row.recorded_at,
                "temperature_c": row.temperature_c,
                "presence": row.presence,
                "lux": row.lux,
            }
            for row in result.fetchall()
            if -40 <= row.temperature_c <= 60
        ]

    async def _load_device_actions(
        self,
        zone_id: uuid.UUID,
        cutoff: datetime,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import select as sa_select

        from backend.models.database import DeviceAction

        result = await self._db.execute(
            sa_select(DeviceAction.created_at, DeviceAction.parameters)
            .where(
                DeviceAction.zone_id == zone_id,
                DeviceAction.action_type == "set_temperature",
                DeviceAction.created_at >= cutoff,
            )
            .order_by(DeviceAction.created_at.asc())
        )
        return [
            {"created_at": row.created_at, "parameters": row.parameters or {}}
            for row in result.fetchall()
        ]

    # ------------------------------------------------------------------
    # Thermal rate analysis
    # ------------------------------------------------------------------

    def _compute_thermal_rates(
        self,
        readings: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute heating rate, cooling rate, response lag, and overshoot."""
        # Build (hours_from_first, temp_c) pairs for rate calculation
        if not readings:
            return {}

        t0 = readings[0]["recorded_at"]
        times_h = [(r["recorded_at"] - t0).total_seconds() / 3600 for r in readings]
        temps_c = [r["temperature_c"] for r in readings]

        # Segment into warming and cooling windows using 15-min rolling deltas
        warming_rates: list[float] = []
        cooling_rates: list[float] = []

        for i, reading in enumerate(readings):
            # Find reading ~15 min later
            target_time = reading["recorded_at"] + timedelta(minutes=15)
            for j in range(i + 1, len(readings)):
                if readings[j]["recorded_at"] >= target_time:
                    dt_h = (readings[j]["recorded_at"] - reading["recorded_at"]).total_seconds() / 3600
                    dT = readings[j]["temperature_c"] - reading["temperature_c"]
                    if dt_h > 0:
                        rate = dT / dt_h
                        if rate > 0.1:
                            warming_rates.append(rate)
                        elif rate < -0.1:
                            cooling_rates.append(rate)
                    break

        heating_rate = _safe_mean(warming_rates)
        cooling_rate = _safe_mean(cooling_rates)

        # Response lag: median time from a set_temperature action to detectable
        # temp change (>= 0.3°C delta in the expected direction)
        lag_minutes_list: list[float] = []
        for action in actions:
            action_time = action["created_at"]
            # Look at temps in the 5-min window before vs 30 min after the action
            before = [
                r["temperature_c"] for r in readings
                if action_time - timedelta(minutes=5) <= r["recorded_at"] < action_time
            ]
            after = [
                (r["recorded_at"], r["temperature_c"]) for r in readings
                if action_time <= r["recorded_at"] <= action_time + timedelta(minutes=45)
            ]
            if not before or len(after) < 2:
                continue
            baseline = sum(before) / len(before)
            for ts, temp in after:
                if abs(temp - baseline) >= 0.3:
                    lag = (ts - action_time).total_seconds() / 60
                    if 1 <= lag <= 45:
                        lag_minutes_list.append(lag)
                    break

        # Overshoot: for each heating period, find the peak above the last setpoint
        overshoot_list: list[float] = []
        for i in range(len(actions) - 1):
            a = actions[i]
            b = actions[i + 1]
            try:
                setpoint_f = float(a["parameters"].get("temperature", 0))
                setpoint_c = (setpoint_f - 32) * 5 / 9 if setpoint_f > 50 else setpoint_f
            except (TypeError, ValueError):
                continue
            window_temps = [
                r["temperature_c"] for r in readings
                if a["created_at"] <= r["recorded_at"] <= b["created_at"]
            ]
            if not window_temps:
                continue
            peak = max(window_temps)
            overshoot = peak - setpoint_c
            if 0 < overshoot < 10:
                overshoot_list.append(overshoot)

        result: dict[str, Any] = {}
        if heating_rate is not None:
            result["heating_rate_c_per_hour"] = round(heating_rate, 3)
        if cooling_rate is not None:
            result["cooling_rate_c_per_hour"] = round(cooling_rate, 3)
        if lag_minutes_list:
            result["response_lag_minutes"] = round(sum(lag_minutes_list) / len(lag_minutes_list), 1)
        if overshoot_list:
            result["typical_overshoot_c"] = round(sum(overshoot_list) / len(overshoot_list), 2)

        # Rolling average trend (last 30 min)
        recent_cutoff = datetime.now(UTC) - timedelta(minutes=30)
        recent = [(t, T) for t, T in zip(times_h, temps_c, strict=False)
                  if readings[times_h.index(t)]["recorded_at"] >= recent_cutoff]
        if len(recent) >= 2:
            rt, rT = zip(*recent, strict=False)
            rate = _linear_rate(list(rt), list(rT))
            if rate is not None:
                result["avg_trend_c_per_min"] = round(rate / 60, 5)

        return result

    # ------------------------------------------------------------------
    # Occupancy analysis
    # ------------------------------------------------------------------

    def _compute_occupancy(self, readings: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute per-hour occupancy scores and detect sleep / nap patterns."""
        # Hourly occupancy score (0.0-1.0) for each hour 0-23
        hour_present: dict[int, list[float]] = {h: [] for h in range(24)}
        hour_lux: dict[int, list[float]] = {h: [] for h in range(24)}
        hour_dark_present: dict[int, list[bool]] = {h: [] for h in range(24)}

        for r in readings:
            h = r["recorded_at"].hour
            presence = r.get("presence")
            lux = r.get("lux")

            if presence is not None:
                hour_present[h].append(1.0 if presence else 0.0)
            if lux is not None:
                hour_lux[h].append(float(lux))
                if presence and lux <= _SLEEP_LUX_THRESHOLD:
                    hour_dark_present[h].append(True)
                else:
                    hour_dark_present[h].append(False)

        occupancy_score_by_hour: dict[str, float] = {}
        for h in range(24):
            scores: list[float] = []
            if hour_present[h]:
                scores.append(_safe_mean(hour_present[h]) * _OCC_PRESENCE_WEIGHT)  # type: ignore[arg-type]
            if hour_lux[h]:
                lux_avg = _safe_mean(hour_lux[h])
                if lux_avg is not None:
                    # High lux → more likely occupied
                    lux_score = min(1.0, lux_avg / 200.0) * _OCC_LUX_WEIGHT
                    scores.append(lux_score)
            if scores:
                occupancy_score_by_hour[str(h)] = round(sum(scores), 3)

        result: dict[str, Any] = {}
        if occupancy_score_by_hour:
            result["occupancy_score_by_hour"] = occupancy_score_by_hour

        # Sleep detection: hours where dark+present is consistently true
        sleep_hours = [
            h for h in range(24)
            if hour_dark_present[h] and
            sum(hour_dark_present[h]) / len(hour_dark_present[h]) >= 0.5
        ]
        # Sleep is a sustained overnight block (midnight-6am is most common)
        overnight = [h for h in sleep_hours if h <= 6 or h >= 21]
        if overnight:
            result["sleep_start"] = f"{min(h for h in overnight if h >= 18):02d}:00"
            result["sleep_end"] = f"{max(h for h in overnight if h <= 9):02d}:00"

        # Nap detection: dark+present concentrated in midday window
        midday_dark_hours = [
            h for h in sleep_hours
            if _NAP_HOUR_START <= h <= _NAP_HOUR_END
        ]
        if len(midday_dark_hours) >= 1:
            result["nap_detected"] = True
            result["typical_nap_hours"] = sorted(midday_dark_hours)
        else:
            result["nap_detected"] = False

        return result


# ---------------------------------------------------------------------------
# Module-level entry point for the scheduler
# ---------------------------------------------------------------------------

async def run_zone_analytics(db: Any, ha_client: Any | None = None) -> None:
    """Entry point called by the apscheduler every 4 hours."""
    logger.info("ZoneAnalytics: starting scheduled analysis run")
    try:
        analyzer = ZoneAnalytics(db, ha_client)
        await analyzer.run_all()
    except Exception as exc:
        logger.error("ZoneAnalytics: run failed: %s", exc)
    finally:
        logger.info("ZoneAnalytics: analysis run complete")
