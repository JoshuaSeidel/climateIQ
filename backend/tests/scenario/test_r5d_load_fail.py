"""§9/§8: runtime state restore failing once (transient DB error) must be retried;
otherwise 'already notified' history is lost for the whole process lifetime."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _Notif

MON = (2026, 11, 9)

async def test_transient_load_failure_not_retried(house: House, monkeypatch: Any) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.add_zone("Bedroom", 70)
    await house.add_schedule("Day", "08:00", "18:00", 72, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 9, 0)
    await main_mod.execute_schedules()
    assert len(n.sent) == 1
    # restart, first restore hits a transient error
    main_mod._last_executed_schedules = set()
    main_mod._runtime_state_loaded = False
    real = main_mod._kv_get
    calls = {"n": 0}
    async def flaky(db: Any, key: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db error")
        return await real(db, key)
    monkeypatch.setattr(main_mod, "_kv_get", flaky)
    house.at(*MON, 9, 1)
    await main_mod.execute_schedules()   # restore fails here
    house.at(*MON, 9, 2)
    await main_mod.execute_schedules()
    house.at(*MON, 9, 3)
    await main_mod.execute_schedules()
    assert len(n.sent) == 1, f"'Schedule Activated' re-sent after restart because restore never retried: {n.sent}"
