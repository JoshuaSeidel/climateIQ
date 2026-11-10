"""AI-mode notification after a heat_cool park says 'Setting to <schedule target>'."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _Notif, _use_llm

MON = (2026, 11, 9)


async def test_park_notification_not_a_setpoint_claim(house: House, monkeypatch: Any) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 72\nREASON: hold")
    await house.set_setting("hvac_control_mode", "heat")  # cool locked out
    await house.add_zone("Bedroom", 77)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Bedroom"])
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat_cool", low_f=68, high_f=75, reading_f=76)
    house.at(*MON, 12, 0)
    await main_mod.execute_active_mode()
    print("MODE", house.ha.mode, house.ha.entities["climate.thermostat"].attributes if "climate.thermostat" in house.ha.entities else house.ha.writes())
    print("NOTIFS", n.sent)
    assert not any("Setting to 72" in m for _, m in n.sent), n.sent
