"""Failed manual write side effects."""
import asyncio
from typing import Any

from backend.integrations.ha_client import HAClientError
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_failed_override_leaves_mode_switched(house: House) -> None:
    from backend.core.manual_control import apply_manual_setpoint
    from backend.core.manual_override import get_active_override

    await house.add_zone("Living Room", 68)
    await house.add_schedule("Day", "06:00", "22:00", 68, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 72, source="dashboard")
    assert house.ha.mode == "heat" and house.ha.setpoint_f == 72

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise HAClientError("HA timeout")

    house.ha.set_temperature_with_hold = _boom  # type: ignore[method-assign]
    house.ha.set_temperature = _boom  # type: ignore[method-assign]
    async with get_session_maker()() as db:
        try:
            await apply_manual_setpoint(db, 64, source="dial")
        except HAClientError:
            pass
    async with get_session_maker()() as db:
        ov = await get_active_override(db)
    print("override", ov and ov.get("setpoint_c"), "thermostat", house.ha.mode, house.ha.setpoint_f)
    assert ov is not None
    house.ha.set_thermostat(reading_f=66)
    house.set_room("Living Room", 66)
    house.at(*MON, 12, 5)
    await house.tick_scheduled()
    assert house.ha.mode == "heat", (
        f"failed request changed the thermostat to {house.ha.mode} @ {house.ha.setpoint_f}F; "
        f"the restored 72F override can no longer heat (rooms 66F)"
    )


async def test_resume_during_failing_override_is_undone(house: House) -> None:
    from backend.core.manual_control import apply_manual_setpoint, resume_automatic_control
    from backend.core.manual_override import get_active_override

    await house.add_zone("Living Room", 68)
    await house.add_schedule("Day", "06:00", "22:00", 68, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 72, source="dashboard")

    parked, release = asyncio.Event(), asyncio.Event()

    async def _slow_fail(*_a: Any, **_k: Any) -> None:
        parked.set()
        await release.wait()
        raise HAClientError("HA timeout")

    house.ha.set_temperature_with_hold = _slow_fail  # type: ignore[method-assign]
    house.ha.set_temperature = _slow_fail  # type: ignore[method-assign]

    async def _dial() -> None:
        async with get_session_maker()() as db:
            try:
                await apply_manual_setpoint(db, 70, source="dial")
            except HAClientError:
                pass

    t = asyncio.create_task(_dial())
    await asyncio.wait_for(parked.wait(), 5)
    # Resume pressed on the dashboard while the dial's request hangs.
    async def _resume() -> None:
        async with get_session_maker()() as db:
            await resume_automatic_control(db, source="quick-action resume")

    r = asyncio.create_task(_resume())
    await asyncio.sleep(0.3)
    release.set()
    await t
    await r
    async with get_session_maker()() as db:
        ov = await get_active_override(db)
    print("after resume", ov)
    assert ov is None, f"Resume was undone: override {ov.get('setpoint_c') if ov else None} restored"
