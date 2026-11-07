"""Convert Home Assistant weather temperatures to Celsius.

HA weather entities report ``temperature`` / ``templow`` in the entity's
``temperature_unit`` (°F on an imperial install).  Every control decision
compares outdoor temperature against Celsius thresholds, so values must be
converted here — reading them raw turned 49°F into "49°C" (≈120°F), flagged
a HEAVY_HOT_DAY in November, and kept the seasonal-lock overrides
permanently tripped.
"""

from __future__ import annotations

from typing import Any


def is_fahrenheit(unit: Any) -> bool:
    return str(unit or "").strip().upper().lstrip("°") == "F"


def to_celsius(value: Any, unit: Any) -> float | None:
    """Convert a weather temperature in ``unit`` to Celsius; None if not numeric."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    v = float(value)
    return round((v - 32.0) * 5.0 / 9.0, 2) if is_fahrenheit(unit) else v
