"""Resume (quick action / dial / chat cancel) in Learn swallows a failed resume_program: reports success, hold never retried."""
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_resume_in_learn_failure_never_retried(house: House, monkeypatch) -> None:
    from backend.core.manual_control import apply_manual_setpoint, resume_automatic_control

    await house.add_zone("Living Room", 68)
    await house.set_mode(SystemMode.learn)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 75, source="dial", duration_hours=3)
    real = house.ha.resume_ecobee_program
    state = {"n": 0}

    async def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("HA hiccup")
        return await real(*a, **k)

    monkeypatch.setattr(house.ha, "resume_ecobee_program", flaky)
    async with get_session_maker()() as db:
        msg = await resume_automatic_control(db, source="dial long-press")
    print(msg)
    for m in (1, 2, 3):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert state["n"] >= 2, f"resume attempted {state['n']}x: user was told '{msg}' but the Ecobee hold (75F) was never handed back"
