"""Time-of-day HVAC direction windows."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.core.mode_windows import (
    ModeWindowState,
    TimeWindow,
    find_active_window,
    satisfied_setpoint_c,
    window_matches,
)

TZ = ZoneInfo("America/New_York")

# 2026-09-01 is a Tuesday (weekday() == 1).
MON = datetime(2026, 8, 31, 12, 0, tzinfo=TZ)
TUE_NOON = datetime(2026, 9, 1, 12, 0, tzinfo=TZ)
TUE_2300 = datetime(2026, 9, 1, 23, 0, tzinfo=TZ)
WED_0200 = datetime(2026, 9, 2, 2, 0, tzinfo=TZ)
WED_0800 = datetime(2026, 9, 2, 8, 0, tzinfo=TZ)


def _c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


# --- window matching -------------------------------------------------------

def test_daytime_window_matches_inside_only():
    w = TimeWindow(start_time="07:00", end_time="21:00")
    assert window_matches(w, TUE_NOON)
    assert not window_matches(w, TUE_2300)
    assert not window_matches(w, WED_0200)


def test_overnight_window_wraps_midnight():
    w = TimeWindow(start_time="21:00", end_time="07:00")
    assert window_matches(w, TUE_2300)   # late evening
    assert window_matches(w, WED_0200)   # early next morning
    assert not window_matches(w, TUE_NOON)
    assert not window_matches(w, WED_0800)


def test_overnight_window_dow_uses_the_starting_day():
    """A Tuesday-night window still covers 02:00 Wednesday."""
    w = TimeWindow(start_time="21:00", end_time="07:00", days_of_week=[1])  # Tue
    assert window_matches(w, TUE_2300)
    assert window_matches(w, WED_0200)
    # A Wednesday-only window must NOT claim Wednesday's small hours.
    w_wed = TimeWindow(start_time="21:00", end_time="07:00", days_of_week=[2])
    assert not window_matches(w_wed, WED_0200)


def test_days_of_week_filter_on_normal_window():
    w = TimeWindow(start_time="07:00", end_time="21:00", days_of_week=[1])  # Tue
    assert window_matches(w, TUE_NOON)
    assert not window_matches(w, MON)


def test_empty_days_means_every_day():
    w = TimeWindow(start_time="07:00", end_time="21:00")
    assert window_matches(w, MON)
    assert window_matches(w, TUE_NOON)


def test_boundaries_are_start_inclusive_end_exclusive():
    w = TimeWindow(start_time="07:00", end_time="21:00")
    assert window_matches(w, TUE_NOON.replace(hour=7, minute=0))
    assert not window_matches(w, TUE_NOON.replace(hour=21, minute=0))


def test_first_matching_window_wins():
    day = TimeWindow(name="day", start_time="07:00", end_time="21:00", allow_heat=False)
    allday = TimeWindow(name="all", start_time="00:00", end_time="00:00")
    assert find_active_window([day, allday], TUE_NOON) is day
    assert find_active_window([day, allday], TUE_2300) is allday
    assert find_active_window([day], TUE_2300) is None


def test_invalid_time_string_is_rejected():
    with pytest.raises(ValueError, match="HH:MM"):
        TimeWindow(start_time="25 oclock", end_time="07:00")


def test_invalid_day_of_week_is_rejected():
    with pytest.raises(ValueError, match="0-6"):
        TimeWindow(start_time="07:00", end_time="21:00", days_of_week=[9])


# --- direction permission --------------------------------------------------

def test_state_allows_only_named_direction():
    st = ModeWindowState(enabled=True, allow_heat=False, allow_cool=True)
    assert not st.allows("heat")
    assert st.allows("cool")
    # Non-directional modes are never blocked.
    assert st.allows("off")
    assert st.allows("heat_cool")
    assert st.allows("")


# --- setpoint parking ------------------------------------------------------

def test_blocked_heat_parks_below_thermostat_reading():
    """Heat fires only while reading < setpoint — park one °F under."""
    parked = satisfied_setpoint_c("heat", desired_temp_c=_c(69), thermostat_c=_c(69))
    assert _f(parked) == pytest.approx(68.0, abs=0.01)


def test_blocked_heat_never_rises_above_the_schedule_target():
    parked = satisfied_setpoint_c("heat", desired_temp_c=_c(65), thermostat_c=_c(75))
    assert _f(parked) == pytest.approx(65.0, abs=0.01)


def test_blocked_cool_parks_above_thermostat_reading():
    parked = satisfied_setpoint_c("cool", desired_temp_c=_c(74), thermostat_c=_c(74))
    assert _f(parked) == pytest.approx(75.0, abs=0.01)


def test_parking_falls_back_to_target_without_a_reading():
    assert satisfied_setpoint_c("heat", desired_temp_c=_c(69), thermostat_c=None) == _c(69)


# --- layer resolution (season overrides beat standalone) -------------------

@pytest.fixture
def stub_configs(monkeypatch):
    """Stub both config loaders so resolution can be tested without a DB."""
    import backend.core.mode_windows as mw
    import backend.core.seasonal_lock as sl

    holder: dict = {"standalone": mw.ModeWindowConfig(), "seasonal": sl.SeasonalLockConfig()}

    async def _load_standalone(_db):
        return holder["standalone"]

    async def _load_seasonal(_db):
        return holder["seasonal"]

    monkeypatch.setattr(mw, "load_config", _load_standalone)
    monkeypatch.setattr(sl, "load_config", _load_seasonal)
    return holder


NIGHT = TimeWindow(name="night", start_time="21:00", end_time="07:00", allow_cool=False)
DAY_NO_HEAT = TimeWindow(name="day", start_time="07:00", end_time="21:00", allow_heat=False)


async def _state(now, **kw):
    from backend.core.mode_windows import compute_window_state
    return await compute_window_state(None, None, now=now, **kw)


@pytest.mark.asyncio
async def test_standalone_windows_block_daytime_heat(stub_configs):
    from backend.core.mode_windows import ModeWindowConfig

    stub_configs["standalone"] = ModeWindowConfig(
        enabled=True, windows=[DAY_NO_HEAT, NIGHT]
    )
    day = await _state(TUE_NOON)
    assert day.active_window == "day"
    assert day.source == "standalone"
    assert not day.allow_heat and day.allow_cool

    night = await _state(TUE_2300)
    assert night.active_window == "night"
    assert night.allow_heat and not night.allow_cool


@pytest.mark.asyncio
async def test_disabled_standalone_config_allows_everything(stub_configs):
    from backend.core.mode_windows import ModeWindowConfig

    stub_configs["standalone"] = ModeWindowConfig(enabled=False, windows=[DAY_NO_HEAT])
    st = await _state(TUE_NOON)
    assert st.allow_heat and st.allow_cool
    assert st.active_window is None


@pytest.mark.asyncio
async def test_uncovered_time_allows_both_directions(stub_configs):
    from backend.core.mode_windows import ModeWindowConfig

    stub_configs["standalone"] = ModeWindowConfig(enabled=True, windows=[DAY_NO_HEAT])
    st = await _state(TUE_2300)  # no window covers 23:00
    assert st.allow_heat and st.allow_cool
    assert st.active_window is None


@pytest.mark.asyncio
async def test_season_windows_override_standalone(stub_configs):
    from backend.core.mode_windows import ModeWindowConfig
    from backend.core.seasonal_lock import Season, SeasonalLockConfig

    stub_configs["standalone"] = ModeWindowConfig(enabled=True, windows=[DAY_NO_HEAT])
    stub_configs["seasonal"] = SeasonalLockConfig(
        enabled=True,
        seasons=[
            Season(
                name="Summer",
                start_month=5, start_day=1, end_month=9, end_day=30,
                preferred_mode="cool",
                # Summer says heat IS fine during the day.
                windows=[TimeWindow(name="summer-day", start_time="00:00", end_time="00:00")],
            )
        ],
    )
    st = await _state(TUE_NOON)  # 2026-09-01 falls in Summer
    assert st.source == "season:Summer"
    assert st.active_window == "summer-day"
    assert st.allow_heat and st.allow_cool


@pytest.mark.asyncio
async def test_season_without_windows_falls_back_to_standalone(stub_configs):
    from backend.core.mode_windows import ModeWindowConfig
    from backend.core.seasonal_lock import Season, SeasonalLockConfig

    stub_configs["standalone"] = ModeWindowConfig(enabled=True, windows=[DAY_NO_HEAT])
    stub_configs["seasonal"] = SeasonalLockConfig(
        enabled=True,
        seasons=[Season(
            name="Summer", start_month=5, start_day=1,
            end_month=9, end_day=30, preferred_mode="cool",
        )],
    )
    st = await _state(TUE_NOON)
    assert st.source == "standalone"
    assert not st.allow_heat


@pytest.mark.asyncio
async def test_escape_valve_reopens_a_blocked_direction(stub_configs):
    """No daytime heat — unless the rooms fall below 60°F."""
    from backend.core.mode_windows import ModeWindowConfig

    stub_configs["standalone"] = ModeWindowConfig(
        enabled=True,
        windows=[TimeWindow(
            name="day", start_time="07:00", end_time="21:00",
            allow_heat=False, escape_below_c=_c(60),
        )],
    )
    warm = await _state(TUE_NOON, zone_avg_c=_c(66))
    assert not warm.allow_heat and not warm.escape_active

    cold = await _state(TUE_NOON, zone_avg_c=_c(58))
    assert cold.allow_heat and cold.escape_active


# --- interaction with the seasonal lock ------------------------------------
# An active window is the more specific statement of intent, so it takes
# precedence: the season lock stands down while a window covers the moment.

SUMMER_OVERNIGHT = TimeWindow(
    name="Overnight", start_time="19:00", end_time="07:00",
    allow_heat=True, allow_cool=True,
)


def _summer_locked_to_cool(windows):
    from backend.core.seasonal_lock import Season, SeasonalLockConfig

    return SeasonalLockConfig(
        enabled=True,
        seasons=[Season(
            name="Summer", start_month=5, start_day=1,
            end_month=9, end_day=30, preferred_mode="cool",
            windows=windows,
        )],
    )


async def _lock_state(now, stub):
    from backend.core.seasonal_lock import compute_lock_state

    return await compute_lock_state(None, None, now=now)


@pytest.mark.asyncio
async def test_window_permitting_heat_suspends_a_cool_season_lock(stub_configs):
    """The reported bug: an overnight window allowing heat, still 'locked to cool'."""
    stub_configs["seasonal"] = _summer_locked_to_cool([SUMMER_OVERNIGHT])

    night = await _lock_state(TUE_2300, stub_configs)
    assert night.window_suspended
    assert night.locked_mode is None       # <- was "cool" before the fix
    assert night.active_window == "Overnight"

    # Sensor-driven selection is then free to pick heat.
    st = await _state(TUE_2300)
    assert st.allow_heat


@pytest.mark.asyncio
async def test_season_lock_still_applies_outside_the_window(stub_configs):
    stub_configs["seasonal"] = _summer_locked_to_cool([SUMMER_OVERNIGHT])

    day = await _lock_state(TUE_NOON, stub_configs)
    assert not day.window_suspended
    assert day.locked_mode == "cool"


@pytest.mark.asyncio
async def test_season_lock_unaffected_when_no_windows_defined(stub_configs):
    stub_configs["seasonal"] = _summer_locked_to_cool([])

    for moment in (TUE_NOON, TUE_2300):
        st = await _lock_state(moment, stub_configs)
        assert st.locked_mode == "cool"
        assert not st.window_suspended


@pytest.mark.asyncio
async def test_window_restricting_to_heat_only_also_suspends_the_lock(stub_configs):
    stub_configs["seasonal"] = _summer_locked_to_cool([
        TimeWindow(name="heat-only", start_time="19:00", end_time="07:00",
                   allow_heat=True, allow_cool=False),
    ])
    night = await _lock_state(TUE_2300, stub_configs)
    assert night.locked_mode is None
    st = await _state(TUE_2300)
    assert st.allow_heat and not st.allow_cool
