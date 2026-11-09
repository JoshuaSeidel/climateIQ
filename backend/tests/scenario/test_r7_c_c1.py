"""create_schedule(overwrite) with no valid entry leaves pending deletes that a later commit flushes."""
from sqlalchemy import select

from backend.models.database import Schedule, get_session_maker
from backend.tests.scenario.harness import House


async def test_overwrite_no_valid_entries_deletes_old(house: House) -> None:
    from backend.api.routes.chat import _execute_tool_call

    zid = await house.add_zone("Living Room", 68)
    await house.add_schedule("Old", "06:00", "22:00", 20, ["Living Room"])
    async with get_session_maker()() as db:
        res = await _execute_tool_call("create_schedule", {
            "zone_id": str(zid), "overwrite": True,
            "entries": [{"day_of_week": "someday", "time": "07:00", "target_c": 21}]}, db)
        assert res["success"] is False
        await db.commit()  # the chat turn's final conversation commit
    async with get_session_maker()() as db:
        names = [s.name for s in (await db.execute(select(Schedule))).scalars().all()]
    assert names == ["Old"], f"old schedule deleted despite failed tool: {names}"
