from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

async def test_learn_expiry_handback_not_retried_after_failure(house: House, monkeypatch) -> None:
    from backend.core.manual_control import apply_manual_setpoint
    await house.add_zone("Living Room", 68)
    await house.set_mode(SystemMode.learn)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    async with get_session_maker()() as db:
        await apply_manual_setpoint(db, 75, source="dial", duration_hours=1)
    real = house.ha.resume_ecobee_program
    state = {"n": 0}
    async def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("HA hiccup")
        return await real(*a, **k)
    monkeypatch.setattr(house.ha, "resume_ecobee_program", flaky)
    house.ha.calls.clear()
    for h, m in ((13, 30), (13, 31), (13, 32), (14, 0)):
        house.at(*MON, h, m)
        await house.tick_all()
    assert state["n"] >= 2 and [c for c in house.ha.calls if c[1] == "resume_program"], (
        f"resume attempted {state['n']}x; the failed handback was never retried (marker already deleted by pop_expired_handback); 75F hold stays in Learn"
    )
