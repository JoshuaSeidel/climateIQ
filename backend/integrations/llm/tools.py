"""Tool (function) definitions in OpenAI tool schema format.

These schemas can be passed directly as the `tools` parameter to OpenAI-style
chat completion APIs (and to litellm).
"""

from __future__ import annotations

from typing import Any


def set_zone_temperature_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "set_zone_temperature",
            "description": "Set a target temperature for a zone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {"type": "string", "description": "Unique zone identifier"},
                    "target_c": {"type": "number", "description": "Target temperature in Celsius"},
                    "hold_minutes": {
                        "type": "integer",
                        "description": "Optional hold duration in minutes",
                    },
                },
                "required": ["zone_id", "target_c"],
                "additionalProperties": False,
            },
        },
    }


def set_device_state_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "set_device_state",
            "description": "Turn a device on/off or set a mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Unique device identifier"},
                    "state": {
                        "type": "string",
                        "description": "Desired state",
                        "enum": ["on", "off", "auto", "heat", "cool", "fan"],
                    },
                    "reason": {"type": "string", "description": "Short rationale for the action"},
                },
                "required": ["device_id", "state"],
                "additionalProperties": False,
            },
        },
    }


def get_zone_status_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_zone_status",
            "description": "Get current status for a zone (sensors, setpoint, hvac state).",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {"type": "string", "description": "Unique zone identifier"},
                    "include_history_minutes": {
                        "type": "integer",
                        "description": "Optional history window in minutes",
                    },
                },
                "required": ["zone_id"],
                "additionalProperties": False,
            },
        },
    }


def get_weather_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Fetch current and short-term forecast weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Latitude"},
                    "lon": {"type": "number", "description": "Longitude"},
                    "units": {
                        "type": "string",
                        "enum": ["metric", "imperial"],
                        "description": "Units for returned weather",
                    },
                },
                "required": ["lat", "lon"],
                "additionalProperties": False,
            },
        },
    }


def create_schedule_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_schedule",
            "description": "Create or update a temperature schedule for a zone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {"type": "string", "description": "Unique zone identifier"},
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone, e.g. America/Los_Angeles",
                    },
                    "entries": {
                        "type": "array",
                        "description": "Schedule entries ordered by time.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "day_of_week": {
                                    "type": "string",
                                    "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                },
                                "time": {"type": "string", "description": "Local time HH:MM"},
                                "target_c": {
                                    "type": "number",
                                    "description": "Target temperature in Celsius",
                                },
                                "mode": {
                                    "type": "string",
                                    "enum": ["auto", "heat", "cool", "off"],
                                    "description": "Optional HVAC mode",
                                },
                            },
                            "required": ["day_of_week", "time", "target_c"],
                            "additionalProperties": False,
                        },
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "If true, replace any existing schedule.",
                    },
                },
                "required": ["zone_id", "timezone", "entries"],
                "additionalProperties": False,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    set_zone_temperature_tool(),
    set_device_state_tool(),
    get_zone_status_tool(),
    get_weather_tool(),
    create_schedule_tool(),
]


def get_climate_tools() -> list[dict[str, Any]]:
    """Return all available climate control tools."""
    return TOOLS.copy()
