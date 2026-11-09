"""infer_zone_occupancy: presence sensor ON + attached light OFF => vacant (0.7 vs 0.4)."""
from __future__ import annotations

from datetime import UTC, datetime

import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.models.database import Sensor, SensorReading, Zone, get_session_maker
from backend.models.enums import SensorType
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_presence_on_light_off_is_occupied(house: House) -> None:
    zid = await house.add_zone("Office", 70)
    house.ha.entities["light.office"] = EntityState(entity_id="light.office", state="off", attributes={})
    sm = get_session_maker()
    import backend.api.dependencies as deps
    deps._ha_client = house.ha
    async with sm() as db:
        z = await db.get(Zone, zid)
        assert z is not None
        z.ha_entities = ["light.office"]
        s = Sensor(zone_id=zid, name="mmwave", type=SensorType.presence_only, ha_entity_id="binary_sensor.office_presence")
        db.add(s)
        await db.flush()
        db.add(SensorReading(sensor_id=s.id, zone_id=zid, recorded_at=datetime.now(UTC), presence=True))
        await db.commit()
    house.at(*MON, 14, 0)
    async with sm() as db:
        occ = await main_mod.infer_zone_occupancy(zid, db)
    assert occ is True, f"occupied (presence sensor on) but inferred {occ}"
