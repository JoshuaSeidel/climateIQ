"""Unit tests for timezone resolution and weather unit conversion."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.core.user_tz import parse_tz, resolve_user_tz
from backend.core.weather_units import to_celsius


class TestParseTz:
    def test_backslash_is_normalised(self) -> None:
        tz = parse_tz("America\\New_York")
        assert tz is not None and str(tz) == "America/New_York"

    def test_dict_value(self) -> None:
        tz = parse_tz({"value": "America/Chicago"})
        assert tz is not None and str(tz) == "America/Chicago"

    def test_utc_and_empty_mean_unset(self) -> None:
        assert parse_tz("UTC") is None
        assert parse_tz("") is None
        assert parse_tz(None) is None

    def test_invalid_is_none(self) -> None:
        assert parse_tz("Mars/Olympus_Mons") is None


class _FakeHA:
    def __init__(self, tz: str) -> None:
        self.tz = tz

    async def get_config(self) -> dict[str, Any]:
        return {"time_zone": self.tz}


class _FakeDB:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def execute(self, _stmt: Any) -> Any:
        row = SimpleNamespace(value=self.value) if self.value is not None else None
        return SimpleNamespace(scalar_one_or_none=lambda: row)


@pytest.mark.parametrize("stored", [{"value": "UTC"}, {"value": "Not/AZone"}, None])
async def test_falls_back_to_ha_timezone(stored: Any) -> None:
    tz = await resolve_user_tz(_FakeDB(stored), _FakeHA("America/New_York"))
    assert str(tz) == "America/New_York"


async def test_stored_timezone_wins() -> None:
    tz = await resolve_user_tz(_FakeDB({"value": "America\\Denver"}), _FakeHA("America/New_York"))
    assert str(tz) == "America/Denver"


class TestToCelsius:
    def test_fahrenheit(self) -> None:
        assert to_celsius(49, "°F") == pytest.approx(9.44, abs=0.01)
        assert to_celsius(32.0, "F") == 0.0

    def test_celsius_passthrough(self) -> None:
        assert to_celsius(21.5, "°C") == 21.5

    def test_non_numeric(self) -> None:
        assert to_celsius(None, "°F") is None
        assert to_celsius("n/a", "°F") is None
        assert to_celsius(True, "°F") is None
