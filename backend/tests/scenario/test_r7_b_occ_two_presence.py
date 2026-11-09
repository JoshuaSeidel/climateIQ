"""Spec 6b.1: 'a presence sensor's last state holds until it changes'.  A zone with TWO presence
sensors: mmwave A reports ON (person sitting), PIR B later reports OFF.  infer_zone_occupancy takes the
single latest row across sensors, so B's OFF masks A's still-valid ON -> room 'vacant'."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import backend.api.dependencies as deps
import backend.api.main as main_mod
from backend.models.database import Sensor, SensorReading, get_session_maker
from backend.models.enums import SensorType
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_two_presence_sensors_one_still_on(house: House) -> None:
    deps._ha_client = house.ha
    zid = await house.add_zone("Office", 70)
    house.at(*MON, 12, 10)
    now = datetime.now(UTC)
    async with get_session_maker()() as db:
        a = Sensor(zone_id=zid, name="mmwave", type=SensorType.presence_only, ha_entity_id="binary_sensor.office_mmwave")
        b = Sensor(zone_id=zid, name="pir", type=SensorType.presence_only, ha_entity_id="binary_sensor.office_pir")
        db.add_all([a, b])
        await db.flush()
        db.add(SensorReading(sensor_id=a.id, zone_id=zid, recorded_at=now - timedelta(minutes=8), presence=True))
        db.add(SensorReading(sensor_id=b.id, zone_id=zid, recorded_at=now - timedelta(minutes=5), presence=False))
        await db.commit()
    async with get_session_maker()() as db:
        occ = await main_mod.infer_zone_occupancy(zid, db)
    assert occ is True, f"mmwave still ON (last state holds) but zone inferred {occ}"
