from typing import Any

import backend.api.main as main_mod
from backend.tests.scenario.harness import THERMOSTAT, House
from backend.tests.scenario.r4_helpers import _Notif


async def test_sched_unavailable_storm(house: House, monkeypatch: Any) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore
    await house.add_zone("Living Room", 66.0)
    await house.add_schedule("Day", "06:00", "22:00", 72, [])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=68)
    house.ha.entities[THERMOSTAT].state = "unavailable"
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
        house.at(2026, 11, 9, 10, i)
        await house.tick_scheduled()
    print("NOTIFS", len(n.sent))
    assert len(n.sent) <= 1
