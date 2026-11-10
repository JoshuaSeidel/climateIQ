import backend.api.main as m
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def _setup(house):
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"


def _restores(house):
    return [
        c
        for c in house.ha.calls
        if c[1] == "set_hvac_mode" and c[2].get("hvac_mode") in ("heat_cool", "auto")
    ]


async def test_unavailable_at_handback(house: House) -> None:
    await _setup(house)
    st = house.ha.entities[m.THERMOSTAT] if hasattr(m, "THERMOSTAT") else None
    from backend.tests.scenario.harness import THERMOSTAT

    st = house.ha.entities[THERMOSTAT]
    st.state = "unavailable"
    house.at(*MON, 17, 0)
    await house.tick_scheduled()
    st.state = "off"  # comes back still off (ClimateIQ's off)
    for mi in range(1, 10):
        house.at(*MON, 17, mi)
        await house.tick_scheduled()
    print("mode after recovery:", house.ha.mode, "restores:", len(_restores(house)))
    assert house.ha.mode != "off", "ClimateIQ's own off never restored after transient unavailable"


async def test_learn_giveup_leaves_stale_record(house: House) -> None:
    await _setup(house)
    rcs = house.ha.call_service
    fail = {"on": True}

    async def cs(domain, service, data=None, target=None, **kw):
        if (
            fail["on"]
            and service == "set_hvac_mode"
            and (data or {}).get("hvac_mode") in ("heat_cool", "auto")
        ):
            raise RuntimeError("HA rejects")
        return await rcs(domain, service, data, target, **kw)

    house.ha.call_service = cs  # type: ignore[method-assign]
    from backend.api.routes.system import apply_system_mode

    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    for mi in range(1, 10):
        house.at(*MON, 12, mi)
        await house.tick_all()
    fail["on"] = False
    # user at Ecobee: heat then own off
    house.ha.set_thermostat(mode="heat")
    house.ha.set_thermostat(mode="off")
    n0 = len(_restores(house))
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.scheduled)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    print("restores after re-entry:", len(_restores(house)) - n0, "record", m._last_mode_switch)
    assert house.ha.mode == "off", "Learn re-entry overrode user's off using stale record"
