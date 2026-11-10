"""WS ingestion: unavailable presence, NaN, duplicate entity."""
from sqlalchemy import select

from backend.api import main as m
from backend.integrations.ha_websocket import HAWebSocketClient
from backend.models.database import (
    Sensor,
    SensorReading,
    SensorType,
    Zone,
    ZoneType,
    get_session_maker,
)


def _parse(entity, state, attrs):
    c = HAWebSocketClient("http://x", "t")
    dom = entity.split(".")[0]
    return c._parse_state_change(entity, dom, {"state": state, "attributes": attrs})


async def _mk(house, entity, n_sensors=1, zones=1):
    async with get_session_maker()() as db:
        zs = []
        for i in range(zones):
            z = Zone(name=f"Z{i}", type=ZoneType.bedroom)
            db.add(z)
            await db.flush()
            zs.append(z)
        for i in range(n_sensors):
            db.add(Sensor(zone_id=zs[i % zones].id, name=f"s{i}", type=SensorType.multisensor, ha_entity_id=entity))
        await db.commit()


async def _rows():
    async with get_session_maker()() as db:
        return (await db.execute(select(SensorReading))).scalars().all()


async def test_unavailable_presence_stored_as_vacant(house):
    await _mk(house, "binary_sensor.office_occupancy")
    ch = _parse("binary_sensor.office_occupancy", "unavailable", {"device_class": "occupancy"})
    await m._handle_ha_state_change(ch)
    rows = await _rows()
    assert not any(r.presence is False for r in rows), "unavailable stored as presence=False (vacant)"


async def test_nan_temperature_stored(house):
    await _mk(house, "sensor.office_temperature")
    ch = _parse("sensor.office_temperature", "nan", {"device_class": "temperature", "unit_of_measurement": "°F"})
    await m._handle_ha_state_change(ch)
    import math
    rows = await _rows()
    assert not any(r.temperature_c is not None and math.isnan(r.temperature_c) for r in rows), "NaN persisted"


async def test_duplicate_entity_two_zones_drops_all(house):
    await _mk(house, "sensor.shared_temperature", n_sensors=2, zones=2)
    ch = _parse("sensor.shared_temperature", "70", {"device_class": "temperature", "unit_of_measurement": "°F"})
    await m._handle_ha_state_change(ch)
    assert len(await _rows()) >= 1, "reading dropped for both sensors (MultipleResultsFound)"
