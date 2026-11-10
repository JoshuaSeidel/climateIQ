"""Learning code: multi-sensor zones, season selection, local-hour prompt keys."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import time_machine

from backend.api.main import _load_weekday_pattern
from backend.core import climate_advisor as ca
from backend.core import user_tz
from backend.core.pattern_engine import PatternEngine
from backend.core.zone_analytics import ZoneAnalytics
from backend.models.database import (
    OccupancyPattern,
    Sensor,
    SensorReading,
    SensorType,
    Zone,
    ZoneType,
    get_session_maker,
)
from backend.models.enums import PatternType, Season
from backend.tests.scenario.harness import House


async def test_two_sensors_at_different_levels_do_not_teach_fake_rates(house: House) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with get_session_maker()() as db:
        zone = Zone(name="Multi", type=ZoneType.bedroom)
        db.add(zone)
        await db.flush()
        sensors = [
            Sensor(zone_id=zone.id, name=f"s{i}", type=SensorType.multisensor) for i in range(2)
        ]
        db.add_all(sensors)
        await db.flush()
        # A flat room: sensor A reads 20.0 and sensor B reads 22.0, alternating each minute.
        for m in range(240):
            db.add(
                SensorReading(
                    sensor_id=sensors[m % 2].id,
                    zone_id=zone.id,
                    recorded_at=t0 + timedelta(minutes=m),
                    temperature_c=(20.0, 22.0)[m % 2],
                )
            )
        await db.commit()
        analytics = ZoneAnalytics(db)
        readings = await analytics._load_sensor_readings(
            [s.id for s in sensors], t0 - timedelta(days=1)
        )
        rates = analytics._compute_thermal_rates(readings, [])
        assert "heating_rate_c_per_hour" not in rates, rates
        assert "cooling_rate_c_per_hour" not in rates, rates
        # ...so the lead time comes from the default rate (3 C/h -> 40 min for 2 C),
        # not from a fake +8 C/h learned from the offset between the two sensors (15 min).
        minutes = PatternEngine(db).get_preconditioning_time(
            "z", current_temp_c=19, target_temp_c=21, hvac_mode="heat", thermal_profile=rates
        )
    assert minutes >= 40


async def test_single_sensor_series_is_unchanged(house: House) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with get_session_maker()() as db:
        zone = Zone(name="One", type=ZoneType.bedroom)
        db.add(zone)
        await db.flush()
        sensor = Sensor(zone_id=zone.id, name="s", type=SensorType.multisensor)
        db.add(sensor)
        await db.flush()
        for m in range(60):
            db.add(
                SensorReading(
                    sensor_id=sensor.id,
                    zone_id=zone.id,
                    recorded_at=t0 + timedelta(minutes=m),
                    temperature_c=18.0 + m * 0.05,
                )
            )
        await db.commit()
        readings = await ZoneAnalytics(db)._load_sensor_readings(
            [sensor.id], t0 - timedelta(days=1)
        )
    assert len(readings) == 60
    assert readings[10]["temperature_c"] == 18.0 + 10 * 0.05


async def test_trend_uses_the_current_season_row(house: House) -> None:
    async with get_session_maker()() as db:
        zone = Zone(name="Seasonal", type=ZoneType.bedroom)
        db.add(zone)
        await db.flush()
        # The winter row was created long ago and is updated in place; the fall
        # row was created later (but is the wrong season in January).
        db.add(
            OccupancyPattern(
                zone_id=zone.id,
                pattern_type=PatternType.weekday,
                season=Season.winter,
                schedule=[{"bucket": "mon:100", "probability": 0.1}],
                confidence=0.5,
                created_at=datetime(2025, 12, 10, tzinfo=UTC),
            )
        )
        db.add(
            OccupancyPattern(
                zone_id=zone.id,
                pattern_type=PatternType.weekday,
                season=Season.fall,
                schedule=[{"bucket": "mon:100", "probability": 0.9}],
                confidence=0.9,
                created_at=datetime(2026, 9, 20, tzinfo=UTC),
            )
        )
        await db.commit()
        with time_machine.travel(datetime(2027, 1, 18, 12, 0, tzinfo=UTC), tick=False):
            pat = await _load_weekday_pattern(db, zone.id)
        assert pat is not None and pat.season == Season.winter
        # No pattern for the season at all: fall back to the newest one.
        with time_machine.travel(datetime(2027, 7, 12, 12, 0, tzinfo=UTC), tick=False):
            pat = await _load_weekday_pattern(db, zone.id)
        assert pat is not None and pat.season == Season.fall


def test_prompt_occupancy_uses_the_users_local_hour(monkeypatch) -> None:
    monkeypatch.setattr(user_tz, "_last_resolved", ZoneInfo("America/New_York"))
    profile = {"heating_rate_c_per_hour": 1.0, "occupancy_score_by_hour": {"20": 0.9, "1": 0.0}}
    # 01:00 UTC is 20:00 in New York; the profile's keys are local hours.
    with time_machine.travel(datetime(2026, 1, 15, 1, 0, tzinfo=UTC), tick=False):
        prompt = ca._build_prompt(
            hvac_mode="heat",
            desired_temp_c=21,
            avg_temp_c=20,
            thermostat_c=20,
            current_setpoint_c=21,
            formula_adjusted_c=21,
            zone_names="A",
            trend=ca.TrendData(),
            thermal_profile=profile,
            outdoor_temp_c=0,
            outdoor_condition="clear",
            last_setpoint_changed_minutes=30,
            temp_unit="F",
        )
    line = next(ln for ln in prompt.splitlines() if ln.startswith("Occupancy now"))
    assert "likely occupied" in line, line


async def _two_sensor_zone(db, name):
    zone = Zone(name=name, type=ZoneType.bedroom)
    db.add(zone)
    await db.flush()
    sensors = [Sensor(zone_id=zone.id, name=f"s{i}", type=SensorType.multisensor) for i in range(2)]
    db.add_all(sensors)
    await db.flush()
    return zone, sensors


async def test_sparse_reporting_sensor_does_not_teach_fake_rates(house: House) -> None:
    """Sensor A every minute at 20.0, sensor B every 45 minutes at 22.0: a flat room."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with get_session_maker()() as db:
        zone, (a, b) = await _two_sensor_zone(db, "Sparse")
        for m in range(8 * 60):
            db.add(
                SensorReading(
                    sensor_id=a.id,
                    zone_id=zone.id,
                    recorded_at=t0 + timedelta(minutes=m),
                    temperature_c=20.0,
                )
            )
            if m % 45 == 0:
                db.add(
                    SensorReading(
                        sensor_id=b.id,
                        zone_id=zone.id,
                        recorded_at=t0 + timedelta(minutes=m, seconds=1),
                        temperature_c=22.0,
                    )
                )
        await db.commit()
        analytics = ZoneAnalytics(db)
        readings = await analytics._load_sensor_readings([a.id, b.id], t0 - timedelta(days=1))
        rates = analytics._compute_thermal_rates(readings, [])
    # B joins once (a single step), but it never drops in and out of the average.
    assert "cooling_rate_c_per_hour" not in rates, rates
    heating = rates.get("heating_rate_c_per_hour")
    assert heating is None or heating < 1.0, rates


async def test_late_sensor_does_not_discard_history(house: House) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with get_session_maker()() as db:
        zone, (a, b) = await _two_sensor_zone(db, "Late")
        for i in range(500):
            db.add(
                SensorReading(
                    sensor_id=a.id,
                    zone_id=zone.id,
                    recorded_at=t0 + timedelta(minutes=10 * i),
                    temperature_c=20.0,
                )
            )
        db.add(
            SensorReading(
                sensor_id=b.id,
                zone_id=zone.id,
                recorded_at=t0 + timedelta(minutes=10 * 450),
                temperature_c=22.0,
            )
        )
        await db.commit()
        readings = await ZoneAnalytics(db)._load_sensor_readings(
            [a.id, b.id], t0 - timedelta(days=1)
        )
    assert len(readings) == 501
