"""Manual override must not make a forbidden direction run (spec §2 'respecting locks', §4.4)."""
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_manual_under_heat_lock_cannot_run_cool(house: House) -> None:
    from backend.core.manual_control import ManualControlError, apply_manual_setpoint
    await house.set_setting("hvac_control_mode", "heat")
    await house.add_zone("Living Room", 76)
    house.ha.set_thermostat(mode="cool", setpoint_f=78, reading_f=76)
    house.at(*MON, 12, 0)
    try:
        async with get_session_maker()() as db:
            await apply_manual_setpoint(db, 70, source="dashboard")
    except ManualControlError as e:
        print("rejected", e)
        return
    print(house.ha.mode, house.ha.setpoint_f, house.ha.calls[-3:])
    assert not house.ha.would_cool(), f"heat-lock house now cooling: mode={house.ha.mode} sp={house.ha.setpoint_f}"


async def test_manual_under_cool_lock_cannot_run_heat(house: House) -> None:
    from backend.core.manual_control import ManualControlError, apply_manual_setpoint
    await house.set_setting("hvac_control_mode", "cool")
    await house.add_zone("Living Room", 66)
    house.ha.set_thermostat(mode="heat", setpoint_f=64, reading_f=66)
    house.at(*MON, 12, 0)
    try:
        async with get_session_maker()() as db:
            await apply_manual_setpoint(db, 72, source="dashboard")
    except ManualControlError as e:
        print("rejected", e)
        return
    assert not house.ha.would_heat(), f"cool-lock house now heating: mode={house.ha.mode} sp={house.ha.setpoint_f}"
