"""Settings API routes for ClimateIQ.

Uses the SystemSetting key-value table for general user settings and
the SystemConfig table (singleton row) for mode, schedule, and LLM config.
The LLM model_discovery module provides live provider introspection.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db, get_ha_client
from backend.config import get_settings as get_app_settings
from backend.integrations import HAClient
from backend.integrations.ha_client import HAClientError
from backend.models.database import SystemConfig, SystemSetting
from backend.models.enums import SystemMode

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class SystemSettingsResponse(BaseModel):
    """Flat view of all system settings."""

    system_name: str = "ClimateIQ"
    current_mode: SystemMode = SystemMode.learn
    timezone: str = "UTC"
    temperature_unit: str = "C"
    default_comfort_temp_min: float = 20.0
    default_comfort_temp_max: float = 24.0
    default_humidity_min: float = 30.0
    default_humidity_max: float = 60.0
    energy_cost_per_kwh: float = 0.12
    currency: str = "USD"
    weather_entity: str = ""
    home_assistant_url: str = ""
    home_assistant_token: str = ""
    climate_entities: str = ""
    sensor_entities: str = ""
    llm_settings: dict[str, Any] = Field(default_factory=dict)
    default_schedule: dict[str, Any] | None = None
    last_synced_at: datetime | None = None


class SystemSettingsUpdate(BaseModel):
    """Partial update for system settings.

    All fields are optional — only supplied fields are applied.
    """

    system_name: str | None = None
    current_mode: SystemMode | None = None
    timezone: str | None = None
    temperature_unit: str | None = None
    default_comfort_temp_min: float | None = None
    default_comfort_temp_max: float | None = None
    default_humidity_min: float | None = None
    default_humidity_max: float | None = None
    energy_cost_per_kwh: float | None = None
    currency: str | None = None
    weather_entity: str | None = None
    home_assistant_url: str | None = None
    home_assistant_token: str | None = None
    climate_entities: str | None = None
    sensor_entities: str | None = None
    default_schedule: dict[str, Any] | None = None
    llm_settings: dict[str, Any] | None = Field(default=None, exclude=True)


class LLMProviderInfo(BaseModel):
    """Information about a single LLM provider and its available models."""

    provider: str
    configured: bool = Field(description="Whether an API key is set")
    models: list[LLMModelInfo] = Field(default_factory=list)


class LLMModelInfo(BaseModel):
    """A single model available from a provider."""

    id: str
    display_name: str | None = None
    context_length: int | None = None


class LLMProvidersResponse(BaseModel):
    """Response listing all LLM providers and their models."""

    providers: list[LLMProviderInfo] = Field(default_factory=list)


class LLMRefreshResponse(BaseModel):
    """Response after refreshing models for a provider."""

    provider: str
    model_count: int
    models: list[LLMModelInfo] = Field(default_factory=list)


class HAEntityInfo(BaseModel):
    """A Home Assistant entity discovered via the HA API."""

    entity_id: str
    name: str
    state: str
    domain: str


# ---------------------------------------------------------------------------
# Keys stored in the SystemSetting key-value table
# ---------------------------------------------------------------------------
_SETTINGS_KEYS: dict[str, Any] = {
    "system_name": "ClimateIQ",
    "timezone": "UTC",
    "temperature_unit": "C",
    "default_comfort_temp_min": 20.0,
    "default_comfort_temp_max": 24.0,
    "default_humidity_min": 30.0,
    "default_humidity_max": 60.0,
    "energy_cost_per_kwh": 0.12,
    "currency": "USD",
    "weather_entity": "",
    "home_assistant_url": "",
    "home_assistant_token": "",
    "climate_entities": "",
    "sensor_entities": "",
}


# ---------------------------------------------------------------------------
# Key-value helpers
# ---------------------------------------------------------------------------
async def _get_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    """Read a single setting from the key-value table."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return default
    return row.value.get("value", default)


async def _set_setting(db: AsyncSession, key: str, value: Any) -> None:
    """Upsert a single setting in the key-value table."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=key, value={"value": value})
        db.add(row)
    else:
        row.value = {"value": value}


# ---------------------------------------------------------------------------
# GET /settings — get all settings
# ---------------------------------------------------------------------------
@router.get("", response_model=SystemSettingsResponse)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemSettingsResponse:
    """Return all system settings as a flat object.

    General settings are read from the SystemSetting key-value table.
    Mode, schedule, and LLM config come from the SystemConfig singleton.
    """
    config = await _get_or_create_config(db)

    # Read general settings from key-value table
    kv: dict[str, Any] = {}
    for key, default in _SETTINGS_KEYS.items():
        kv[key] = await _get_setting(db, key, default)

    return SystemSettingsResponse(
        system_name=kv["system_name"],
        current_mode=config.current_mode,
        timezone=kv["timezone"],
        temperature_unit=kv["temperature_unit"],
        default_comfort_temp_min=kv["default_comfort_temp_min"],
        default_comfort_temp_max=kv["default_comfort_temp_max"],
        default_humidity_min=kv["default_humidity_min"],
        default_humidity_max=kv["default_humidity_max"],
        energy_cost_per_kwh=kv["energy_cost_per_kwh"],
        currency=kv["currency"],
        weather_entity=kv["weather_entity"],
        home_assistant_url=str(kv["home_assistant_url"]),
        home_assistant_token=kv["home_assistant_token"],
        climate_entities=kv["climate_entities"],
        sensor_entities=kv["sensor_entities"],
        llm_settings=config.llm_settings or {},
        default_schedule=config.default_schedule,
        last_synced_at=config.last_synced_at,
    )


# ---------------------------------------------------------------------------
# PUT /settings — update settings
# ---------------------------------------------------------------------------
@router.put("", response_model=SystemSettingsResponse)
async def update_settings(
    payload: SystemSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemSettingsResponse:
    """Update system settings. Only supplied fields are changed."""
    config = await _get_or_create_config(db)
    update_data = payload.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update",
        )

    # Handle first-class SystemConfig columns
    if "current_mode" in update_data:
        config.current_mode = update_data.pop("current_mode")
    if "default_schedule" in update_data:
        config.default_schedule = update_data.pop("default_schedule")

    # Remaining keys go into the SystemSetting key-value table
    for key, value in update_data.items():
        if key in _SETTINGS_KEYS:
            await _set_setting(db, key, value)

    config.last_synced_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(config)

    return await get_settings(db)


# ---------------------------------------------------------------------------
# GET /settings/ha/entities — discover Home Assistant entities
# ---------------------------------------------------------------------------
_DEFAULT_ENTITY_DOMAINS = [
    "sensor",
    "binary_sensor",
    "climate",
    "fan",
    "cover",
    "switch",
    "weather",
]


@router.get("/ha/entities", response_model=list[HAEntityInfo])
async def list_ha_entities(
    ha_client: Annotated[HAClient, Depends(get_ha_client)],
    domain: Annotated[str | None, Query(description="Filter by entity domain")] = None,
) -> list[HAEntityInfo]:
    """Return Home Assistant entities, optionally filtered by domain.

    If no *domain* is specified the default set of HVAC-relevant domains is
    returned (sensor, binary_sensor, climate, fan, cover, switch, weather).
    """
    try:
        states = await ha_client.get_states()
    except HAClientError as exc:
        logger.error("Failed to fetch HA states: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to reach Home Assistant",
        ) from exc

    domains = [domain] if domain else _DEFAULT_ENTITY_DOMAINS

    entities: list[HAEntityInfo] = []
    for entity in states:
        for d in domains:
            if entity.entity_id.startswith(f"{d}."):
                entities.append(
                    HAEntityInfo(
                        entity_id=entity.entity_id,
                        name=entity.attributes.get("friendly_name", entity.entity_id),
                        state=entity.state,
                        domain=d,
                    )
                )
                break
    return entities


# ---------------------------------------------------------------------------
# GET /settings/llm/providers — list LLM providers with available models
# ---------------------------------------------------------------------------
@router.get("/llm/providers", response_model=LLMProvidersResponse)
async def list_llm_providers(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LLMProvidersResponse:
    """List all supported LLM providers and their currently cached models.

    A provider is marked as 'configured' if its API key is set in the
    environment or application settings.
    """
    from backend.integrations.llm.model_discovery import discover_models

    app_settings = get_app_settings()

    provider_keys: dict[str, str] = {
        "anthropic": app_settings.anthropic_api_key,
        "openai": app_settings.openai_api_key,
        "gemini": app_settings.gemini_api_key,
        "grok": app_settings.grok_api_key,
    }

    # Local providers (no API key needed)
    local_providers = {
        "ollama": str(app_settings.ollama_url),
        "llamacpp": str(app_settings.llamacpp_url),
    }

    providers: list[LLMProviderInfo] = []

    # Cloud providers
    for provider_name, api_key in provider_keys.items():
        configured = bool(api_key)
        models: list[LLMModelInfo] = []

        if configured:
            try:
                discovered = discover_models(provider_name, api_key=api_key)
                models = [
                    LLMModelInfo(
                        id=m.id,
                        display_name=m.display_name,
                        context_length=m.context_length,
                    )
                    for m in discovered
                ]
            except Exception:
                logger.debug("Model discovery failed for %s", provider_name, exc_info=True)

        providers.append(
            LLMProviderInfo(
                provider=provider_name,
                configured=configured,
                models=models,
            )
        )

    # Local providers
    for provider_name, base_url in local_providers.items():
        models = []
        try:
            discovered = discover_models(provider_name, base_url=base_url)
            models = [
                LLMModelInfo(
                    id=m.id,
                    display_name=m.display_name,
                    context_length=m.context_length,
                )
                for m in discovered
            ]
            configured = len(models) > 0
        except Exception:
            configured = False

        providers.append(
            LLMProviderInfo(
                provider=provider_name,
                configured=configured,
                models=models,
            )
        )

    return LLMProvidersResponse(providers=providers)


# ---------------------------------------------------------------------------
# POST /settings/llm/providers/{provider}/refresh — refresh models from API
# ---------------------------------------------------------------------------
@router.post(
    "/llm/providers/{provider}/refresh",
    response_model=LLMRefreshResponse,
)
async def refresh_provider_models(
    provider: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LLMRefreshResponse:
    """Force-refresh the model list for a specific provider.

    Clears the in-memory cache and re-fetches from the provider's API.
    """
    from backend.integrations.llm.model_discovery import discover_models

    supported = {"anthropic", "openai", "gemini", "grok", "ollama", "llamacpp"}
    provider = provider.lower().strip()
    if provider not in supported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Must be one of: {sorted(supported)}",
        )

    app_settings = get_app_settings()

    # Resolve API key / base URL
    api_key: str | None = None
    base_url: str | None = None

    if provider == "anthropic":
        api_key = app_settings.anthropic_api_key or None
    elif provider == "openai":
        api_key = app_settings.openai_api_key or None
    elif provider == "gemini":
        api_key = app_settings.gemini_api_key or None
    elif provider == "grok":
        api_key = app_settings.grok_api_key or None
    elif provider == "ollama":
        base_url = str(app_settings.ollama_url)
    elif provider == "llamacpp":
        base_url = str(app_settings.llamacpp_url)

    try:
        discovered = discover_models(
            provider,
            api_key=api_key,
            base_url=base_url,
            force_refresh=True,
        )
    except Exception as exc:
        logger.exception("Model refresh failed for %s", provider)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to refresh models for '{provider}': {exc}",
        ) from exc

    models = [
        LLMModelInfo(
            id=m.id,
            display_name=m.display_name,
            context_length=m.context_length,
        )
        for m in discovered
    ]

    return LLMRefreshResponse(
        provider=provider,
        model_count=len(models),
        models=models,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_or_create_config(db: AsyncSession) -> SystemConfig:
    """Return the singleton SystemConfig row, creating it if absent."""
    result = await db.execute(select(SystemConfig).limit(1))
    config = result.scalar_one_or_none()
    if config is not None:
        return config

    config = SystemConfig(
        current_mode=SystemMode.learn,
        default_schedule=None,
        llm_settings={},
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


__all__ = ["router"]
