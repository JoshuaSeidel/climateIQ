"""§9.1b: last heat/cool/off switch survives restarts.  A transient DB error on the
first restore is swallowed and `_mode_switch_loaded` stays True -> never retried, so the
reversal cooldown and Learn's 'did ClimateIQ turn it off' check stay lost for the process."""
from __future__ import annotations

from typing import Any

import backend.api.main as main_mod
from backend.tests.scenario.harness import House


async def test_mode_switch_restore_not_retried(house: House, monkeypatch: Any) -> None:
    await main_mod._record_mode_switch("climate.x", "off")
    main_mod._last_mode_switch.clear()
    main_mod._mode_switch_loaded = False
    real = main_mod._kv_get
    calls = {"n": 0}
    async def flaky(db: Any, key: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db error")
        return await real(db, key)
    monkeypatch.setattr(main_mod, "_kv_get", flaky)
    await main_mod._ensure_mode_switch_loaded()   # fails
    await main_mod._ensure_mode_switch_loaded()   # should retry
    assert "climate.x" in main_mod._last_mode_switch, "last mode switch never restored after one transient error"
