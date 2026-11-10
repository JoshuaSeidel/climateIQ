import backend.integrations.ha_client as hc
from backend.api.routes.system import emergency_shutoff
from backend.config import get_settings
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_shutoff_hands_back_to_ecobee(house: House, monkeypatch) -> None:
    """Spec §2: emergency shutoff = off + Learn; Learn means the Ecobee's own
    program owns the thermostat (resume program, restore occupancy features).
    apply_system_mode(learn) does that; emergency_shutoff sets Learn directly and skips it,
    leaving ClimateIQ's offset-compensated hold and disabled Ecobee occupancy features."""
    s = get_settings()
    monkeypatch.setattr(s, "home_assistant_token", "tok", raising=False)
    class _Ctx:
        async def __aenter__(self): return house.ha
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(hc, "HAClient", lambda *a, **k: _Ctx())
    await house.add_zone("Bed", 60)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=60)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    house.ha.calls.clear()
    async with get_session_maker()() as db:
        await emergency_shutoff(db)
    eco = [c for c in house.ha.calls if c[0] == "ecobee"]
    print("RESULT calls", [(c[0], c[1]) for c in house.ha.calls], eco)
    assert any(c[1] == "resume_program" for c in eco), "ClimateIQ's setpoint hold left on the Ecobee"
    assert any(c[1] == "set_occupancy_modes" for c in eco), "Ecobee occupancy features left disabled"
