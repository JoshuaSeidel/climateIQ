from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_persistent_undo_failure_storm(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    real = house.ha.call_service

    async def cs(domain, service, data=None, target=None, **kw):
        if service == "set_hvac_mode" and (data or {}).get("hvac_mode") in ("heat_cool", "auto"):
            raise RuntimeError("HA rejects")
        return await real(domain, service, data, target, **kw)

    house.ha.call_service = cs  # type: ignore[method-assign]
    n0 = len([c for c in house.ha.calls if c[1] == "resume_program"])
    for m in range(0, 30):
        house.at(*MON, 17, m)
        await house.tick_scheduled()
    n = len([c for c in house.ha.calls if c[1] == "resume_program"]) - n0
    print("resume_program calls over 30 gap ticks:", n)
    assert n <= 3


async def test_learn_persistent(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    real = house.ha.call_service

    async def cs(domain, service, data=None, target=None, **kw):
        if service == "set_hvac_mode" and (data or {}).get("hvac_mode") in ("heat_cool", "auto"):
            raise RuntimeError("HA rejects")
        return await real(domain, service, data, target, **kw)

    house.ha.call_service = cs  # type: ignore[method-assign]
    from backend.api.routes.system import apply_system_mode

    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    n0 = len([c for c in house.ha.calls if c[1] == "resume_program"])
    for m in range(1, 30):
        house.at(*MON, 12, m)
        await house.tick_all()
    n = len([c for c in house.ha.calls if c[1] == "resume_program"]) - n0
    print("learn resume_program calls over 29 ticks:", n)
    assert n <= 3
