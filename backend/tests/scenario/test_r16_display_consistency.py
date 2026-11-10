"""Dashboard data: unit default with no saved row, and no 'Set:' target for an off schedule."""

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from backend.api.main import app
from backend.api.middleware import RateLimitMiddleware
from backend.config import SETTINGS
from backend.models.database import close_db, get_session_maker, init_db
from backend.tests.scenario.harness import House


def _reset_rate_limit() -> None:
    """The limiter's per-IP budget is shared by the whole pytest process."""
    node = getattr(app, "middleware_stack", None)
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            node._requests.clear()
            return
        node = getattr(node, "app", None)


async def test_settings_unit_default_matches_addon_option_and_off_schedule_has_no_target(
    house: House, monkeypatch
) -> None:
    import backend.api.main as m

    # A ZoneManager / pooled connection left by an earlier test may be bound to a
    # closed event loop: start from a clean engine (known test-isolation quirk).
    monkeypatch.setattr(m.app_state, "zone_manager", None)
    await close_db()
    await init_db()
    _reset_rate_limit()
    house.at(2026, 1, 12, 10, 0)
    await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "08:00", "12:00", 70.5)
    await house.add_schedule("Off", "12:00", "14:00", 72, hvac_mode="off")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # With no saved unit row, /settings must agree with what /system/override
        # converts to (the add-on option), or the UI labels °F numbers as °C.
        async with get_session_maker()() as db:
            await db.execute(text("DELETE FROM system_settings WHERE key='temperature_unit'"))
            await db.commit()
        settings = (await c.get("/api/v1/settings")).json()
        expected = "F" if str(SETTINGS.temperature_unit).upper() == "F" else "C"
        assert settings["temperature_unit"] == expected

        # During an "off" schedule there is no target to display.
        house.at(2026, 1, 12, 13, 0)
        ov = (await c.get("/api/v1/system/override")).json()
        assert ov["schedule_target_temp"] is None, ov["schedule_target_temp"]

        # A normal schedule still shows its target.
        house.at(2026, 1, 12, 9, 0)
        ov = (await c.get("/api/v1/system/override")).json()
        assert ov["schedule_target_temp"] is not None
