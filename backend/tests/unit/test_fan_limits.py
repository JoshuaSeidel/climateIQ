"""Unit tests for the quiet-hours fan speed cap."""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from typing import Any

import pytest

import backend.api.main as main_mod
from backend.core.fan_limits import FanNightCap, cap_for_time


class TestCapForTime:
    cfg = FanNightCap(enabled=True, start="19:00", end="07:00", max_pct=50)

    def test_before_window_uncapped(self) -> None:
        assert cap_for_time(self.cfg, time(18, 59)) is None

    def test_at_start_capped(self) -> None:
        assert cap_for_time(self.cfg, time(19, 0)) == 50

    def test_after_midnight_capped(self) -> None:
        assert cap_for_time(self.cfg, time(2, 30)) == 50

    def test_end_is_exclusive(self) -> None:
        assert cap_for_time(self.cfg, time(7, 0)) is None

    def test_disabled(self) -> None:
        cfg = FanNightCap(enabled=False)
        assert cap_for_time(cfg, time(22, 0)) is None

    def test_same_day_window(self) -> None:
        cfg = FanNightCap(enabled=True, start="13:00", end="15:00", max_pct=30)
        assert cap_for_time(cfg, time(14, 0)) == 30
        assert cap_for_time(cfg, time(16, 0)) is None


class FakeHA:
    """Minimal HA client: tracks fan percentages and service calls."""

    def __init__(self, pct: dict[str, int]) -> None:
        self.pct = dict(pct)
        self.calls: list[tuple[str, str, int | None]] = []

    async def get_state(self, entity_id: str) -> Any:
        p = self.pct[entity_id]
        return SimpleNamespace(state="on" if p else "off", attributes={"percentage": p})

    async def call_service(
        self, domain: str, service: str, target: dict[str, Any], data: dict[str, Any] | None = None
    ) -> None:
        eid = target["entity_id"]
        new = 0 if service == "turn_off" else int((data or {})["percentage"])
        self.pct[eid] = new
        self.calls.append((domain, service, new))


@pytest.fixture(autouse=True)
def _clear_fan_state() -> Any:
    main_mod._fan_baseline.clear()
    yield
    main_mod._fan_baseline.clear()


async def test_apply_fan_target_respects_cap() -> None:
    ha = FakeHA({"fan.nursery": 0})
    changed, _ = await main_mod._apply_fan_target(ha, "fan.nursery", 100, True, max_pct=50)
    assert changed
    assert ha.pct["fan.nursery"] == 50


async def test_apply_fan_target_never_raises_above_cap_from_user_baseline() -> None:
    # User has the fan at 70% themselves — cap means we don't raise it, and
    # we don't lower it either.
    ha = FakeHA({"fan.nursery": 70})
    changed, _ = await main_mod._apply_fan_target(ha, "fan.nursery", 100, True, max_pct=50)
    assert not changed
    assert ha.pct["fan.nursery"] == 70


async def test_sweep_lowers_fan_we_raised() -> None:
    ha = FakeHA({"fan.nursery": 20})
    await main_mod._apply_fan_target(ha, "fan.nursery", 100, True)
    assert ha.pct["fan.nursery"] == 100
    await main_mod._enforce_fan_night_cap(ha, 50)
    assert ha.pct["fan.nursery"] == 50
    assert main_mod._fan_baseline["fan.nursery"]["override_active"] is True


async def test_sweep_restores_baseline_when_cap_below_it() -> None:
    ha = FakeHA({"fan.nursery": 60})
    await main_mod._apply_fan_target(ha, "fan.nursery", 100, True)
    await main_mod._enforce_fan_night_cap(ha, 50)
    # Never below the user's own 60% baseline.
    assert ha.pct["fan.nursery"] == 60
    assert main_mod._fan_baseline["fan.nursery"]["override_active"] is False


async def test_sweep_leaves_hand_adjusted_fan_alone() -> None:
    ha = FakeHA({"fan.nursery": 0})
    await main_mod._apply_fan_target(ha, "fan.nursery", 100, True)
    ha.pct["fan.nursery"] = 70  # user changed it by hand (beyond the 20% step tolerance)
    await main_mod._enforce_fan_night_cap(ha, 50)
    assert ha.pct["fan.nursery"] == 70


async def test_sweep_ignores_fans_we_never_touched() -> None:
    ha = FakeHA({"fan.nursery": 100})
    await main_mod._enforce_fan_night_cap(ha, 50)
    assert ha.calls == []


async def test_apply_leaves_hand_changed_fan_alone() -> None:
    ha = FakeHA({"fan.nursery": 30})
    await main_mod._apply_fan_target(ha, "fan.nursery", 100, True)
    assert ha.pct["fan.nursery"] == 100
    main_mod._fan_baseline["fan.nursery"]["last_change_at"] = None  # skip cooldown
    ha.pct["fan.nursery"] = 40  # user turned it down by hand
    changed, note = await main_mod._apply_fan_target(ha, "fan.nursery", 100, True)
    assert not changed and note.startswith("user_changed")
    assert ha.pct["fan.nursery"] == 40
    assert "fan.nursery" not in main_mod._fan_baseline


async def test_stepped_fan_readback_is_not_a_hand_change() -> None:
    ha = FakeHA({"fan.nursery": 0})
    await main_mod._apply_fan_target(ha, "fan.nursery", 75, True)
    ha.pct["fan.nursery"] = 67  # 3-speed fan rounds 75 → 67
    await main_mod._enforce_fan_night_cap(ha, 50)
    assert ha.pct["fan.nursery"] == 50
