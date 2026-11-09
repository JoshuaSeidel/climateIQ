"""parse_command unit-suffix regex edge cases (no word boundary on the 'set' pattern; 'celsius' ignored on 'by N')."""
import uuid
from types import SimpleNamespace
from typing import Any

from backend.api.routes.chat import parse_command

Z: Any = [SimpleNamespace(id=uuid.uuid4(), name="Living Room")]


async def test_set_for_tonight_celsius_user() -> None:
    r = await parse_command("set living room to 21 for tonight", Z, "C")
    assert r and abs(r["temperature_c"] - 21.0) < 0.01, r  # 'f' of "for" read as Fahrenheit


async def test_warmer_by_degrees_celsius_word_fahrenheit_user() -> None:
    r = await parse_command("make it warmer in living room by 3 degrees celsius", Z, "F")
    assert r and abs(r["amount_c"] - 3.0) < 0.01, r  # \b drops 'celsius' -> default F -> 1.67
