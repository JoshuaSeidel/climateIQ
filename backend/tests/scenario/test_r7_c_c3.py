"""'make it warmer' builds an absolute whole-house override from a days-old single-sensor reading (spec §9.2, temp_compensation uses a 30 min freshness rule)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.models.database import Sensor, SensorReading, get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_adjust_uses_stale_reading(house: House) -> None:
    from backend.api.routes.chat import CommandRequest, execute_command

    await house.add_zone("Living Room", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        s = (await db.execute(select(Sensor))).scalars().first()
        assert s is not None
        db.add(SensorReading(sensor_id=s.id, recorded_at=datetime.now(UTC) - timedelta(days=2),
                             temperature_c=10.0))
        await db.commit()
    async with get_session_maker()() as db:
        res = await execute_command(CommandRequest(command="make it warmer in living room"), db)
    # Anchored on the live 68F sensor (+2F), never on the 2-day-old 10C (50F) reading.
    assert res.success and house.ha.setpoint_f is not None
    assert abs(house.ha.setpoint_f - 70.0) < 0.6, f"setpoint {house.ha.setpoint_f}"
