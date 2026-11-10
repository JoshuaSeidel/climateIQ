"""Emergency shutoff: really turns thermostat off, stays off, Learn/handback don't undo it."""
import backend.api.main as main_mod
import backend.integrations.ha_client as hc
from backend.api.routes.system import apply_system_mode, emergency_shutoff
from backend.config import get_settings
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


def _patch(house, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "home_assistant_token", "tok", raising=False)

    class _Ctx:
        async def __aenter__(self_inner):
            return house.ha
        async def __aexit__(self_inner, *a):
            return False
    monkeypatch.setattr(hc, "HAClient", lambda *a, **k: _Ctx())


async def test_shutoff_stays_off_all_loops(house: House, monkeypatch) -> None:
    _patch(house, monkeypatch)
    await house.add_zone("Bed", 60)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=60)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    async with get_session_maker()() as db:
        r = await emergency_shutoff(db)
    print(r)
    assert house.ha.mode == "off"
    for m in range(1, 4):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert house.ha.mode == "off"


async def test_shutoff_after_climateiq_off_then_learn_reselect(house: House, monkeypatch) -> None:
    """ClimateIQ's off-schedule turned it off (recorded). User hits emergency
    shutoff (already off, system -> Learn). Re-selecting Learn / gap handback
    must not re-enable HVAC: the off is now the user's."""
    _patch(house, monkeypatch)
    await house.add_zone("Bed", 60)
    await house.add_schedule("Away", "09:00", "17:00", 62, ["Bed"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=60)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    print("mode after learn reselect:", house.ha.mode)
    assert house.ha.mode == "off", "emergency shutoff undone by Learn handback"


async def test_shutoff_when_climateiq_had_it_off_in_scheduled_then_scheduled_gap(house: House, monkeypatch) -> None:
    """Emergency shutoff while ClimateIQ-off (vacation). Then user flips to Scheduled and back to Learn."""
    _patch(house, monkeypatch)
    await house.add_zone("Bed", 60)
    await house.add_schedule("Away", "09:00", "17:00", 62, ["Bed"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=60)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    print(main_mod._last_mode_switch)
    assert main_mod._last_mode_switch.get("climate.thermostat", ("",))[0] != "off", "emergency off still recorded as ClimateIQ's own old off"
