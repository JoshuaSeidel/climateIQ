"""Rolled-back manual write records ClimateIQ 'off' -> Learn turns a user-chosen off back on."""
from typing import Any

from backend.integrations.ha_client import HAClientError
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_failed_manual_on_user_off_thermostat_then_learn(house: House) -> None:
    import backend.api.main as main_mod
    from backend.api.routes.system import apply_system_mode
    from backend.core.manual_control import apply_manual_setpoint

    await house.add_zone("Living Room", 60)
    house.ha.set_thermostat(mode="off", setpoint_f=68, reading_f=60)  # user turned it off
    house.at(*MON, 12, 0)

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise HAClientError("HA timeout")

    house.ha.set_temperature_with_hold = _boom  # type: ignore[method-assign]
    house.ha.set_temperature = _boom  # type: ignore[method-assign]
    async with get_session_maker()() as db:
        try:
            await apply_manual_setpoint(db, 72, source="dial")
        except HAClientError:
            pass
    assert house.ha.mode == "off"
    print("last switch", main_mod._last_mode_switch)
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    assert house.ha.mode == "off", f"user's 'off' was turned into {house.ha.mode} by Learn"
