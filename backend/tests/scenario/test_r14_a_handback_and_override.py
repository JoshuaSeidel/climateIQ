"""R14-A probes: a single transient HA failure while handing back a ClimateIQ 'off', and override accepted with no sensors."""

from backend.core.manual_control import apply_manual_setpoint
from backend.integrations.ha_client import EntityState
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


def _fail_once_hvac(house: House, mode: str) -> dict:
    st = {"armed": False}
    real = house.ha.call_service

    async def cs(domain, service, data=None, target=None, **kw):
        if st["armed"] and service == "set_hvac_mode" and (data or {}).get("hvac_mode") == mode:
            st["armed"] = False
            raise RuntimeError("transient HA failure")
        return await real(domain, service, data, target, **kw)

    house.ha.call_service = cs  # type: ignore[method-assign]
    return st


async def test_gap_handback_set_mode_fails_once_stays_off(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    st = _fail_once_hvac(house, "heat_cool")
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    st["armed"] = True
    for h, m in ((17, 0), (17, 1), (17, 30), (18, 0), (19, 0)):
        house.at(*MON, h, m)
        await house.tick_scheduled()
    assert house.ha.mode != "off", (
        "one failed set_hvac_mode at gap handback left the thermostat off for the whole gap"
    )


async def test_learn_handover_set_mode_fails_once_stays_off(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    st = _fail_once_hvac(house, "heat_cool")
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    st["armed"] = True
    from backend.api.routes.system import apply_system_mode

    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    for m in range(1, 20):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert house.ha.mode != "off", "Learn handover: failed undo of ClimateIQ's off is never retried"


async def test_override_with_no_sensors_on_off_thermostat_does_nothing(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    eid = "sensor.living_room_temperature"
    house.ha.entities[eid] = EntityState(entity_id=eid, state="unavailable", attributes={})
    async with get_session_maker()() as db:
        try:
            await apply_manual_setpoint(db, 72, source="dial")
            accepted = True
        except Exception:
            accepted = False
    # Either reject, or the override must actually hold (thermostat on).
    assert (not accepted) or house.ha.mode != "off", (
        "override accepted + pauses loops, but thermostat still off"
    )
