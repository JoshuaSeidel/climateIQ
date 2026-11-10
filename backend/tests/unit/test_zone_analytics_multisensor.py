"""Multi-sensor zones: the zone-average series must not turn sensor membership changes
(a change-only sensor going quiet and returning, one dying, one joining) or offsets
between sensors into temperature changes."""

import asyncio
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.core.zone_analytics import ZoneAnalytics

Row = namedtuple("Row", "sensor_id recorded_at temperature_c presence lux")
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _DB:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, _query: Any) -> _Result:
        return _Result(self._rows)


def _rates(rows: list[Any]) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: r.recorded_at)
    analytics = ZoneAnalytics(_DB(rows))  # type: ignore[arg-type]
    readings = asyncio.run(analytics._load_sensor_readings(["a", "b"], T0))
    return analytics._compute_thermal_rates(readings, [])


def _series(
    sid: str,
    offset: float,
    every: int,
    minutes: int,
    temp_at,
    silent: tuple[int, int] | None = None,
):
    rows = []
    for m in range(0, minutes, every):
        if silent and silent[0] <= m < silent[1]:
            continue
        rows.append(Row(sid, T0 + timedelta(minutes=m), temp_at(m) + offset, None, None))
    return rows


def _flat(_m: int) -> float:
    return 20.0


def test_sensor_silent_for_7h_teaches_no_rates() -> None:
    rows = _series("a", 0, 1, 24 * 60, _flat) + _series(
        "b", 2.0, 1, 24 * 60, _flat, silent=(420, 840)
    )
    rates = _rates(rows)
    assert "heating_rate_c_per_hour" not in rates and "cooling_rate_c_per_hour" not in rates, rates


def test_sensor_that_dies_teaches_no_rates() -> None:
    rows = _series("a", 0, 1, 24 * 60, _flat) + _series(
        "b", 2.0, 1, 24 * 60, _flat, silent=(120, 10**6)
    )
    rates = _rates(rows)
    assert "heating_rate_c_per_hour" not in rates and "cooling_rate_c_per_hour" not in rates, rates


def test_real_ramp_is_learned_through_offset_sensors_of_different_cadence() -> None:
    def ramp(m: int) -> float:
        return 18.0 + m / 60.0  # 1 C/h

    for cadences in ((1, 7), (1, 45), (7, 45)):
        rows = _series("a", 0.0, cadences[0], 6 * 60, ramp) + _series(
            "b", 2.0, cadences[1], 6 * 60, ramp
        )
        rate = _rates(rows).get("heating_rate_c_per_hour")
        assert rate is not None and 0.8 <= rate <= 1.2, (cadences, rate)


def test_one_outage_does_not_inflate_a_quiet_zones_rate() -> None:
    def quiet(m: int) -> float:
        # 0.2 C/h ramp for the first hour, flat afterwards
        return 20.0 + (min(m, 60) / 60.0) * 0.2

    base = _series("a", 0.0, 1, 24 * 60, quiet) + _series("b", 2.0, 1, 24 * 60, quiet)
    with_outage = _series("a", 0.0, 1, 24 * 60, quiet) + _series(
        "b", 2.0, 1, 24 * 60, quiet, silent=(420, 840)
    )
    clean = _rates(base).get("heating_rate_c_per_hour")
    outage = _rates(with_outage).get("heating_rate_c_per_hour")
    assert clean is not None and outage is not None
    assert abs(outage - clean) < 0.05, (clean, outage)
