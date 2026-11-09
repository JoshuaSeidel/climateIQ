from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_override_expiry_in_learn_keeps_hold(house: House) -> None:
    from backend.api.routes.system import get_override_status
    from backend.core.manual_control import apply_manual_setpoint

    await house.add_zone("Living Room", 68)
    await house.set_mode(SystemMode.learn)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 75, source="dial", duration_hours=1)
    house.ha.calls.clear()
    for h in (13, 14, 15):
        house.at(*MON, h, 30)
        await house.tick_all()
    async with get_session_maker()() as db:
        st = await get_override_status(db)
    print("status", st["is_override_active"], house.ha.setpoint_f, house.ha.preset if hasattr(house.ha, "preset") else house.ha.entities["climate.thermostat"].attributes.get("preset_mode"))
    assert not st["is_override_active"]
    assert [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"Learn: 1h override expired 2.5h ago but the 75F hold is still on the Ecobee "
        f"(setpoint {house.ha.setpoint_f}, preset "
        f"{house.ha.entities['climate.thermostat'].attributes.get('preset_mode')}); its program never resumes"
    )
