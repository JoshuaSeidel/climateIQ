"""Known minors: adjust amount regex; create_schedule partial commit."""
from datetime import UTC, datetime

from sqlalchemy import select

from backend.models.database import Schedule, Sensor, SensorReading, get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_warmer_amount_from_zone_name_digit(house: House) -> None:
    from backend.api.routes.chat import CommandRequest, execute_command

    await house.add_zone("Room 5", 68)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        s = (await db.execute(select(Sensor))).scalars().first()
        assert s is not None
        db.add(SensorReading(sensor_id=s.id, recorded_at=datetime.now(UTC), temperature_c=20.0))
        await db.commit()
    async with get_session_maker()() as db:
        res = await execute_command(CommandRequest(command="make it warmer in room 5"), db)
    print(res, house.ha.setpoint_f)
    # default is +2 in the user's unit (68F -> 70F); zone digit 5 made it +5F
    assert res.success and house.ha.setpoint_f is not None and abs(house.ha.setpoint_f - 70.0) < 0.6, f"setpoint {house.ha.setpoint_f}"


async def test_create_schedule_partial_commit(house: House) -> None:
    from backend.api.routes.chat import _execute_tool_call

    zid = await house.add_zone("Living Room", 68)
    await house.add_schedule("Old", "06:00", "22:00", 20, ["Living Room"])
    async with get_session_maker()() as db:
        res = await _execute_tool_call("create_schedule", {
            "zone_id": str(zid), "overwrite": True,
            "entries": [
                {"day_of_week": "mon", "time": "07:00", "target_c": 21},
                {"day_of_week": "tue", "time": "07:00", "target_c": 72},  # F typed as C -> rejected
            ]}, db)
        print(res)
        assert res["success"] is False
        await db.commit()  # any later tool call in the same chat turn commits the shared session
    async with get_session_maker()() as db:
        names = [s.name for s in (await db.execute(select(Schedule))).scalars().all()]
    print(names)
    assert sorted(names) == ["Old"], f"partial commit: {names}"
