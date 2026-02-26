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


def save_memory_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Permanently save a fact, preference, or routine to the ClimateIQ memory store "
                "so it is available in future conversations and influences AI climate decisions. "
                "Use this when the user explicitly asks to save or remember something, or when "
                "they share important house facts, daily routines, or comfort preferences that "
                "were not already extracted automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directive": {
                        "type": "string",
                        "description": "The fact or preference to remember (max 200 chars). Be specific and concise.",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "preference",
                            "constraint",
                            "comfort",
                            "schedule_hint",
                            "routine",
                            "occupancy",
                            "house_info",
                            "energy",
                        ],
                        "description": (
                            "preference/comfort/constraint = temperature likes/dislikes; "
                            "routine/occupancy = when people are home/sleeping; "
                            "house_info = physical characteristics of the home; "
                            "schedule_hint = implicit schedule info; "
                            "energy = energy saving preferences."
                        ),
                    },
                    "zone_name": {
                        "type": "string",
                        "description": "Optional zone name this memory applies to (e.g. 'Master Bedroom'). Omit for whole-house facts.",
                    },
                },
                "required": ["directive", "category"],
                "additionalProperties": False,
            },
        },
    }


def get_zone_history_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_zone_history",
            "description": (
                "Get historical temperature and humidity data for one zone or all zones over a time window. "
                "Use this to answer questions about overnight temperature maintenance, drift, "
                "trends, how stable rooms were, or any question involving past readings. "
                "Omit zone_id to get all zones at once (e.g. to compare rooms overnight)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Zone identifier. Omit to query ALL active zones at once.",
                    },
                    "hours_ago": {
                        "type": "integer",
                        "description": (
                            "How many hours back to look from now. "
                            "Use 8 for 'last night', 24 for 'yesterday/last day', "
                            "1 for 'last hour'. Defaults to 8."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_schedules_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_schedules",
            "description": (
                "Get all configured temperature schedules. Returns each schedule's name, "
                "target temperature, HVAC mode, days of week, start/end times, priority, "
                "and which zones it applies to. Use this to answer questions about what "
                "the scheduled temperatures are, what runs at night, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to schedules that include this zone.",
                    },
                    "enabled_only": {
                        "type": "boolean",
                        "description": "If true, only return enabled schedules. Default false.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_user_feedback_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_user_feedback",
            "description": (
                "Get user comfort feedback history: too_hot, too_cold, too_humid, too_dry, "
                "or comfortable ratings per zone. Use this to understand recurring comfort "
                "issues, patterns of discomfort, and whether zones are meeting user needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific zone.",
                    },
                    "hours_ago": {
                        "type": "integer",
                        "description": "How many hours back to look. Default 168 (1 week).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_sensor_status_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_sensor_status",
            "description": (
                "Get sensor health details: which sensors are configured per zone, their "
                "last_seen timestamp (to detect offline/stale sensors), HA entity ID, "
                "type, and calibration offsets. Use this to diagnose missing data or "
                "sensor problems."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific zone.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_occupancy_patterns_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_occupancy_patterns",
            "description": (
                "Get learned occupancy patterns per zone — when rooms are typically occupied "
                "by time of day, day of week, and season. Use this to understand routines "
                "and make scheduling recommendations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific zone.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_ai_decisions_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_ai_decisions",
            "description": (
                "Get the AI climate advisor's recent decision log: what setpoints were "
                "commanded, why (full reasoning), what triggered each action, and what "
                "the outcome was. Use this to audit system behavior or explain what "
                "happened and why."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific zone.",
                    },
                    "hours_ago": {
                        "type": "integer",
                        "description": "How many hours back to look. Default 24.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max decisions to return. Default 20, max 100.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_device_actions_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_device_actions",
            "description": (
                "Get HVAC thermostat commands and actions taken for a zone over a time window. "
                "Use this to see what setpoints were commanded, why, and when — useful for "
                "understanding what the system did overnight or in response to temperature changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Unique zone identifier (optional — omit for all zones)",
                    },
                    "hours_ago": {
                        "type": "integer",
                        "description": "How many hours back to look. Defaults to 8.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_zones_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_zones",
            "description": (
                "Get all configured zones (rooms) with their current temperature, "
                "humidity, occupancy, sensor count, and device count. Use this to "
                "understand what rooms are configured, which are active, and their "
                "current conditions at a glance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a single zone by ID.",
                    },
                    "include_inactive": {
                        "type": "boolean",
                        "description": "If true, include inactive zones. Default false.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_devices_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_devices",
            "description": (
                "Get all HVAC/thermostat devices configured in the system. Returns "
                "device type, Home Assistant entity ID, zone assignment, primary status, "
                "and capabilities. Use to understand what hardware is available and how "
                "it is wired to zones."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to devices in a specific zone.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_energy_data_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_energy_data",
            "description": (
                "Get estimated HVAC energy usage based on device action history. "
                "Returns kWh estimates and cost per zone. Use this for questions about "
                "energy efficiency, cost, or which zones use the most energy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_ago": {
                        "type": "integer",
                        "description": "Lookback window in hours. Default 24, max 720.",
                    },
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific zone.",
                    },
                    "cost_per_kwh": {
                        "type": "number",
                        "description": "Electricity cost per kWh for cost estimates. Default 0.12.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def get_comfort_scores_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_comfort_scores",
            "description": (
                "Get comfort scores (0-100) for each zone based on how well temperature "
                "and humidity stayed in the comfortable range. A score of 100 means "
                "always in range. Use this for questions about how comfortable zones "
                "are, which rooms need improvement, or comfort trends over time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_ago": {
                        "type": "integer",
                        "description": "Lookback window in hours. Default 24, max 720.",
                    },
                    "zone_id": {
                        "type": "string",
                        "description": "Optional: filter to a specific zone.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def set_system_mode_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "set_system_mode",
            "description": (
                "Change the ClimateIQ operating mode. "
                "learn = observe only, no changes; "
                "scheduled = follow user-defined schedules; "
                "follow_me = track occupancy, heat only occupied zones; "
                "active = full AI control every 5 minutes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["learn", "scheduled", "follow_me", "active"],
                        "description": "The operating mode to switch to.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short rationale for the mode change.",
                    },
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
    }


def set_override_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "set_override",
            "description": (
                "Set a manual temperature override on the thermostat. The hold persists "
                "until canceled or the next scheduled event. Use this when the user "
                "asks to set the temperature right now to a specific value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "temperature": {
                        "type": "number",
                        "description": "Target temperature in the user's display unit (°F or °C).",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short rationale for the override.",
                    },
                },
                "required": ["temperature"],
                "additionalProperties": False,
            },
        },
    }


def cancel_override_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "cancel_override",
            "description": (
                "Cancel any active manual temperature override, returning the thermostat "
                "to its normal schedule. Use when the user asks to cancel a hold, resume "
                "the schedule, or stop a manual override."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def delete_schedule_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "delete_schedule",
            "description": (
                "Delete a schedule by its ID. First call get_schedules to find the "
                "schedule ID you want to delete. This permanently removes the schedule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "UUID of the schedule to delete.",
                    },
                },
                "required": ["schedule_id"],
                "additionalProperties": False,
            },
        },
    }


def delete_directive_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "delete_directive",
            "description": (
                "Delete a saved memory/directive. You can identify it by directive_id "
                "(preferred, from get_schedules or the memory panel) or by exact "
                "directive_text. Use when the user asks to forget or remove a specific "
                "memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directive_id": {
                        "type": "string",
                        "description": "UUID of the directive to delete.",
                    },
                    "directive_text": {
                        "type": "string",
                        "description": "Exact text of the directive to delete (used if ID not known).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    set_zone_temperature_tool(),
    set_device_state_tool(),
    set_override_tool(),
    cancel_override_tool(),
    set_system_mode_tool(),
    get_zone_status_tool(),
    get_zones_tool(),
    get_devices_tool(),
    get_zone_history_tool(),
    get_device_actions_tool(),
    get_schedules_tool(),
    get_user_feedback_tool(),
    get_sensor_status_tool(),
    get_occupancy_patterns_tool(),
    get_ai_decisions_tool(),
    get_energy_data_tool(),
    get_comfort_scores_tool(),
    get_weather_tool(),
    create_schedule_tool(),
    delete_schedule_tool(),
    save_memory_tool(),
    delete_directive_tool(),
]


def get_climate_tools() -> list[dict[str, Any]]:
    """Return all available climate control tools."""
    return TOOLS.copy()
