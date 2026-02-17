"""ClimateIQ LLM integration package.

This package provides:
- ClimateIQLLMProvider: provider abstraction with fallbacks
- Dynamic model discovery
- Prompt templates
- Tool (function) schemas
- Token/cost tracking
"""

from .cost_tracker import CostTracker, UsageRecord
from .model_discovery import (
    ModelInfo,
    discover_many,
    discover_models,
    fetch_anthropic_models,
    fetch_gemini_models,
    fetch_grok_models,
    fetch_llamacpp_models,
    fetch_ollama_models,
    fetch_openai_models,
)
from .provider import ClimateIQLLMProvider, ProviderSettings
from .tools import (
    TOOLS,
    create_schedule_tool,
    get_weather_tool,
    get_zone_status_tool,
    set_device_state_tool,
    set_zone_temperature_tool,
)

__all__ = [
    "TOOLS",
    "ClimateIQLLMProvider",
    "CostTracker",
    "ModelInfo",
    "ProviderSettings",
    "UsageRecord",
    "create_schedule_tool",
    "discover_many",
    "discover_models",
    "fetch_anthropic_models",
    "fetch_gemini_models",
    "fetch_grok_models",
    "fetch_llamacpp_models",
    "fetch_ollama_models",
    "fetch_openai_models",
    "get_weather_tool",
    "get_zone_status_tool",
    "set_device_state_tool",
    "set_zone_temperature_tool",
]
