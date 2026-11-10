from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import THERMOSTAT, House
from backend.tests.scenario.r4_helpers import _Notif, _use_llm

MON = (2026, 11, 9)


async def test_unavailable_thermostat_switch_storm(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 72\nREASON: ok")
    n = _Notif()
    main_mod._notification_service = n  # type: ignore
    await house.add_zone("Living Room", 66.0, comfort_preferences={"temp_min": 20, "temp_max": 23})
    await house.add_schedule("Day", "06:00", "22:00", 72, [])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=68)
    # thermostat goes unavailable; HA silently skips entity service calls to unavailable entities
    st = house.ha.entities[THERMOSTAT]
    st.state = "unavailable"
    orig = house.ha.call_service

    async def cs(domain, service, data=None, target=None, **kw):
        p = dict(data or {})
        p.update(target or {})
        if domain == "climate" and p.get("entity_id") == THERMOSTAT:
            house.ha.calls.append((domain, service, p))
            return []
        return await orig(domain, service, data, target, **kw)

    house.ha.call_service = cs  # type: ignore
    for i in range(8):
        house.at(*MON, 10, i * 5)
        await main_mod.execute_active_mode()
    sw = [c for c in house.ha.calls if c[1] == "set_hvac_mode"]
    print("SWITCH CALLS", len(sw), "NOTIFS", list(n.sent))
    assert len(n.sent) <= 1, (
        f"{len(n.sent)} mode-switch notifications for an unavailable thermostat"
    )
