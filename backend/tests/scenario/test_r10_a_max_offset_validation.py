"""§5.4 / settings: max_temp_offset_f is unvalidated; a negative value is accepted and heat stops running."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.routes.settings import SystemSettingsUpdate
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


def test_negative_offset_rejected_by_api() -> None:
    with pytest.raises(ValidationError):
        SystemSettingsUpdate(max_temp_offset_f=-5.0)


async def test_negative_offset_heat_still_runs(house: House) -> None:
    await house.set_setting("max_temp_offset_f", -5.0)
    await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "08:00", "20:00", 72, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=60, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print(house.ha.mode, house.ha.setpoint_f, house.ha.action)
    assert house.ha.would_heat()
