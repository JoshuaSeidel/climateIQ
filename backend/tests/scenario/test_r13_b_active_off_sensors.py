"""Active mode + off schedule + partial sensor dropout: Active loop turns the thermostat on (heat_cool) while an off schedule governs; maintenance turns it off again -> flap + notification every 5 min."""
from typing import Any

import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _Notif, _use_llm

SUN = (2026, 3, 8)

async def test_active_off_partial_sensors(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 72\nFAN_ACTIONS:\nREASON: ok")
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    for z, t in (("Living Room", 73.0), ("Bedroom", 66.0), ("Office", 70.0)):
        await house.add_zone(z, t, comfort_preferences={"temp_min": 20, "temp_max": 23})
    await house.add_schedule("Day", "06:30", "22:00", 72, [])
    await house.add_schedule("Away", "02:45", "10:45", 60, ["Living Room", "Bedroom"], hvac_mode="off", priority=5)
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=72)
    modes = []
    for t in range(2 * 60 + 50, 4 * 60 + 30):
        house.at(*SUN, t // 60, t % 60)
        if t >= 3 * 60 + 56 and t < 4 * 60 + 11:   # two of three sensors drop out for 15 min
            for z in ("Living Room", "Bedroom"):
                eid = f"sensor.{z.lower().replace(' ', '_')}_temperature"
                house.ha.entities[eid] = EntityState(entity_id=eid, state="unavailable", attributes={})
        else:
            for z, v in (("Living Room", 73.0), ("Bedroom", 66.0), ("Office", 70.0)):
                house.set_room(z, v)
        await house.tick_all() if t % 5 == 0 else await house.tick_scheduled()
        modes.append(house.ha.mode)
    switches = [c for c in house.ha.calls if c[1] == "set_hvac_mode"]
    print("SWITCHES", [(c[2]["hvac_mode"]) for c in switches], "notifs", len(n.sent))
    assert not any(c[2]["hvac_mode"] != "off" for c in switches[1:]), f"Active mode turned thermostat on during off schedule: {[c[2]['hvac_mode'] for c in switches]}"
