"""§8: 'sensor offline (once per episode)' + 'already notified survives restarts'."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import backend.api.main as main_mod
from backend.models.database import Sensor, get_session_maker
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _Notif


async def test_sensor_offline_renotified_after_restart(house: House) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.add_zone("Bedroom", 70)
    async with get_session_maker()() as db:
        s = (await db.execute(__import__("sqlalchemy").select(Sensor))).scalars().first()
        assert s is not None
        s.last_seen = datetime.now(UTC) - timedelta(hours=3)
        await db.commit()
        ent = s.ha_entity_id
    house.ha.entities.pop(ent, None)     # HA reports nothing / not found
    main_mod._offline_notified.clear()
    main_mod._offline_loaded = False
    await main_mod.check_sensor_health()
    assert len(n.sent) == 1, n.sent
    main_mod._offline_notified.clear()   # process restart (add-on update / reboot)
    main_mod._offline_loaded = False
    await main_mod.check_sensor_health()
    assert len(n.sent) == 1, f"offline alert re-sent after restart: {n.sent}"
