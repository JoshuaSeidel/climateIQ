"""HA reports the pre-switch mode for the rest of the tick after set_hvac_mode(off)."""
import backend.api.main as main_mod
from backend.tests.scenario.harness import THERMOSTAT, House

MON = (2026, 11, 9)

async def test_stale_read_clears_off_record(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    ha = house.ha
    real_get, real_call = ha.get_state, ha.call_service
    stale = {"on": False, "st": None}
    async def get_state(eid):
        if eid == THERMOSTAT and stale["on"]:
            return stale["st"]
        return await real_get(eid)
    async def call_service(domain, service, data=None, target=None, **kw):
        if service == "set_hvac_mode" and (data or {}).get("hvac_mode") == "off":
            stale["st"] = await real_get(THERMOSTAT)  # pre-switch snapshot
            stale["on"] = True
        return await real_call(domain, service, data, target, **kw)
    ha.get_state = get_state  # type: ignore[method-assign]
    ha.call_service = call_service  # type: ignore[method-assign]
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    stale["on"] = False
    print("rec", main_mod._last_mode_switch)
    assert ha.mode == "off"
    house.at(*MON, 12, 1)
    await house.tick_scheduled()
    for h in (17, 18):
        house.at(*MON, h, 1)
        await house.tick_scheduled()
    assert ha.mode != "off", "handback skipped: off record cleared by stale read"
