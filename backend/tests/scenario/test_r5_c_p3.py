"""POST /chat/command: regex-parsed command is unit-converted twice (F user)."""
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def _run(house: House, cmd: str):
    from backend.api.routes.chat import CommandRequest, execute_command
    async with get_session_maker()() as db:
        return await execute_command(CommandRequest(command=cmd), db)


async def test_set_temperature_command_f_user(house: House) -> None:
    await house.add_zone("Living Room", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    res = await _run(house, "Set living room to 72 degrees")
    print(res, house.ha.mode, house.ha.setpoint_f)
    assert res.success and house.ha.setpoint_f == 72.0, f"{res.message}; thermostat {house.ha.setpoint_f}"


async def test_set_temperature_command_explicit_unit(house: House) -> None:
    await house.add_zone("Living Room", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    res = await _run(house, "Set living room to 72 f")
    print(res, house.ha.mode, house.ha.setpoint_f)
    assert res.success and house.ha.setpoint_f == 72.0, f"{res.message}; thermostat {house.ha.setpoint_f}"


async def test_warmer_command_f_user(house: House) -> None:
    await house.add_zone("Living Room", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    # a reading must exist in DB for adjust path
    from datetime import UTC, datetime

    from sqlalchemy import select

    from backend.models.database import Sensor, SensorReading
    async with get_session_maker()() as db:
        s = (await db.execute(select(Sensor))).scalars().first()
        assert s is not None
        db.add(SensorReading(sensor_id=s.id, recorded_at=datetime.now(UTC), temperature_c=20.0))
        await db.commit()
    res = await _run(house, "make it warmer in living room by 4 degrees")
    print(res, house.ha.mode, house.ha.setpoint_f)
    assert res.success and house.ha.setpoint_f is not None and abs(house.ha.setpoint_f - 72.0) < 0.6, f"{res.message}; thermostat {house.ha.setpoint_f}"
