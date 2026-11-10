"""Two presence sensors: B (held ON, last row 2 h ago), A (OFF, row 1 min ago).
A sensor's last state holds up to 24 h (spec 6b.1) -> occupied (any-True)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import backend.api.main as main_mod
from backend.models.database import Sensor, SensorReading, get_session_maker
from backend.models.enums import SensorType
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_held_on_sensor_ignored_when_other_has_recent_off(house: House) -> None:
    zid = await house.add_zone("Office", 70)
    house.at(*MON, 14, 0)
    sm = get_session_maker()
    async with sm() as db:
        a = Sensor(zone_id=zid, name="a", type=SensorType.presence_only, ha_entity_id="binary_sensor.a")
        b = Sensor(zone_id=zid, name="b", type=SensorType.presence_only, ha_entity_id="binary_sensor.b")
        db.add_all([a, b])
        await db.flush()
        now = datetime.now(UTC)
        db.add(SensorReading(sensor_id=b.id, zone_id=zid, recorded_at=now - timedelta(hours=2), presence=True))
        db.add(SensorReading(sensor_id=a.id, zone_id=zid, recorded_at=now - timedelta(minutes=1), presence=False))
        await db.commit()
    async with sm() as db:
        occ = await main_mod.infer_zone_occupancy(zid, db)
    assert occ is True, f"sensor B's held ON state ignored: {occ}"
