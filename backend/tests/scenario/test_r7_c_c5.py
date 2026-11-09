"""A failed/rejected manual request must change nothing (spec §2) but restore_override/set_override re-arm the gap handback -> Ecobee hold wiped."""
from typing import Any

from backend.integrations.ha_client import HAClientError
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_failed_manual_in_gap_rearms_handback(house: House) -> None:
    from backend.core.manual_control import apply_manual_setpoint

    await house.add_zone("Living Room", 68)  # no schedules -> gap, Ecobee owns it
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_all()  # one-time handback
    house.ha.calls.clear()
    house.at(*MON, 12, 1)
    await house.tick_all()
    assert not [c for c in house.ha.calls if c[1] == "resume_program"], "handback repeated without cause"

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise HAClientError("HA timeout")

    house.ha.set_temperature_with_hold = _boom  # type: ignore[method-assign]
    house.ha.set_temperature = _boom  # type: ignore[method-assign]
    async with get_session_maker()() as db:
        try:
            await apply_manual_setpoint(db, 72, source="dial")
        except HAClientError:
            pass
    house.ha.calls.clear()
    house.at(*MON, 12, 2)
    await house.tick_all()
    n = [c for c in house.ha.calls if c[1] == "resume_program"]
    assert not n, f"failed manual request changed nothing, yet next tick handed the Ecobee back again: {n}"
