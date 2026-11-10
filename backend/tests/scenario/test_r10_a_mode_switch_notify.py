"""§8: every heat<->cool switch notifies; service throttle swallows a real switch."""
from __future__ import annotations

import backend.api.main as main_mod
from backend.services.notification_service import NotificationService
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_urgent_reversal_notification_swallowed(house: House) -> None:
    main_mod._notification_service = NotificationService(house.ha)
    await house.add_zone("Bedroom", 66)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Bedroom"])
    house.ha.set_thermostat(mode="cool", setpoint_f=74, reading_f=70)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()           # cool -> heat
    assert house.ha.mode == "heat"
    house.set_room("Bedroom", 78)
    house.ha.set_thermostat(reading_f=70, setpoint_f=76)  # heating against hot rooms
    assert house.ha.action == "heating"
    house.at(*MON, 12, 5)
    await house.tick_scheduled()           # heat -> cool (urgent)
    assert house.ha.mode == "cool"
    notes = [c for c in house.ha.calls if c[0] == "notify" and c[2].get("title") == "Thermostat mode"]
    print([c[2].get("message") for c in notes])
    assert len(notes) == 2, f"2 heat<->cool switches, {len(notes)} notifications"
