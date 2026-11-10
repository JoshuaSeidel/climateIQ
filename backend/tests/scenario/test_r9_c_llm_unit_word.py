"""LLM fallback parse: {"unit": "fahrenheit"} / "°F" is upper()-ed to FAHRENHEIT / °F, never equals "F",
so 72 is treated as 72 C (later rejected as out of safety range) and deltas are not x5/9."""
import uuid
from types import SimpleNamespace

from backend.api.routes.chat import _normalize_parsed_command

Z = [SimpleNamespace(id=uuid.uuid4(), name="Living Room")]


def test_set_temperature_unit_word():
    for u in ("fahrenheit", "Fahrenheit", "°F", "degF"):
        r = _normalize_parsed_command(
            {"action": "set_temperature", "zone": "Living Room", "temperature": 72, "unit": u}, Z, "C")
        assert r and abs(r["temperature_c"] - 22.22) < 0.05, (u, r)


def test_adjust_unit_word():
    r = _normalize_parsed_command(
        {"action": "adjust_temperature", "zone": "Living Room", "amount": 3, "unit": "fahrenheit"}, Z, "C")
    assert r and abs(r["amount_c"] - 1.667) < 0.01, r
