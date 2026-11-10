"""max_temp_offset_f: UI offers 0 ("disable") and 0.5C steps; validator rejects them, and 0 is read back as 8."""
from backend.api.routes.settings import SystemSettingsUpdate
from backend.core.temp_compensation import get_max_offset_setting
from backend.models.database import get_session_maker
from backend.tests.scenario.harness import House


def test_ui_zero_accepted() -> None:
    # Settings.tsx: <Input min="0"> + "Set to 0 to disable offset compensation"
    SystemSettingsUpdate(max_temp_offset_f=0.0)


def test_ui_half_degree_celsius_accepted() -> None:
    # step=0.5 in C -> 0.5*9/5 = 0.9 F
    SystemSettingsUpdate(max_temp_offset_f=0.9)


async def test_stored_zero_means_disabled(house: House) -> None:
    await house.set_setting("max_temp_offset_f", 0.0)
    async with get_session_maker()() as db:
        assert await get_max_offset_setting(db) == 0.0
