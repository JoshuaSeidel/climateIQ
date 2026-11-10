"""Switching to Learn (POST /system/mode, chat set_system_mode) swallows a failed resume_program:
the hold ClimateIQ left on the Ecobee stays forever (Learn writes nothing), unlike Resume/expiry
which re-arm a retry marker."""
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_mode_switch_to_learn_failed_resume_never_retried(house: House, monkeypatch) -> None:
    from backend.api.routes.system import apply_system_mode

    await house.add_zone("Living Room", 66)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=66)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()          # ClimateIQ writes its (offset) hold
    real = house.ha.resume_ecobee_program
    state = {"n": 0}

    async def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("HA hiccup")
        return await real(*a, **k)

    monkeypatch.setattr(house.ha, "resume_ecobee_program", flaky)
    house.ha.calls.clear()
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    for h, m in ((12, 2), (12, 3), (12, 4), (12, 30)):
        house.at(*MON, h, m)
        await house.tick_all()
    assert state["n"] >= 2 and [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"resume attempted {state['n']}x and never retried; ClimateIQ's hold stays on the Ecobee in Learn"
    )
