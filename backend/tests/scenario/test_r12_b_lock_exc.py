"""hvac_control_mode=heat: a seasonal-lock failure now blocks mode selection entirely (previously not consulted)."""
import backend.core.seasonal_lock as sl
from backend.tests.scenario.harness import House


async def test_lock_exc(house: House, monkeypatch) -> None:
    await house.set_setting("hvac_control_mode", "heat")
    await house.add_zone("Living Room", 65.0)
    await house.add_schedule("Day", "06:30", "22:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="cool", setpoint_f=80, reading_f=65)
    async def boom(*a, **k): raise RuntimeError("weather read failed")
    monkeypatch.setattr(sl, "compute_lock_state", boom)
    house.at(2026, 11, 9, 12, 0)
    await house.tick_scheduled()
    print("MODES", house.ha.mode)
    assert house.ha.mode == "heat"
