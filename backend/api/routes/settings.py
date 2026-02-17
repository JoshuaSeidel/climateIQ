"""Settings API routes for ClimateIQ."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.config import SETTINGS
from backend.models.database import SystemConfig, SystemSetting
from backend.models.enums import SystemMode

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# KV-store defaults — keys persisted in the system_settings table
# ---------------------------------------------------------------------------

_KV_DEFAULTS: dict[str, Any] = {
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
    "climate_entities": "",
    "sensor_entities": "",
    "energy_entity": "",
}

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SystemSettingsResponse(BaseModel):
    system_name: str = "ClimateIQ"
    current_mode: str = "learn"
    timezone: str = "UTC"
    temperature_unit: str = "C"
    default_comfort_temp_min: float = 20.0
    default_comfort_temp_max: float = 24.0
    default_humidity_min: float = 30.0
    default_humidity_max: float = 60.0
    energy_cost_per_kwh: float = 0.12
    currency: str = "USD"
    weather_entity: str = ""
    climate_entities: str = ""
    sensor_entities: str = ""
    energy_entity: str = ""
    home_assistant_url: str = ""
    home_assistant_token: str = ""
    llm_settings: dict[str, Any] = {}
    default_schedule: dict[str, Any] | None = None
    last_synced_at: str | None = None


class SystemSettingsUpdate(BaseModel):
    system_name: str | None = None
    timezone: str | None = None
    temperature_unit: str | None = None
    default_comfort_temp_min: float | None = None
    default_comfort_temp_max: float | None = None
    default_humidity_min: float | None = None
    default_humidity_max: float | None = None
    energy_cost_per_kwh: float | None = None
    currency: str | None = None
    weather_entity: str | None = None
    climate_entities: str | None = None
    sensor_entities: str | None = None
    energy_entity: str | None = None


class HAEntityInfo(BaseModel):
    entity_id: str
    name: str
    state: str


class LLMProviderInfo(BaseModel):
    provider: str
    configured: bool = False
    models: list[str] = []


class LLMProvidersResponse(BaseModel):
    providers: list[LLMProviderInfo]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_system_config(session: AsyncSession) -> SystemConfig:
    """Return the singleton SystemConfig row, creating it if absent."""
    result = await session.execute(select(SystemConfig).limit(1))
    config = result.scalar_one_or_none()
    if config:
        return config
    config = SystemConfig(current_mode=SystemMode.learn, default_schedule=None, llm_settings={})
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config


async def _read_all_kv(session: AsyncSession) -> dict[str, Any]:
    """Read every row from system_settings and return a merged dict with defaults."""
    result = await session.execute(select(SystemSetting))
    rows = result.scalars().all()

    values: dict[str, Any] = dict(_KV_DEFAULTS)
    for row in rows:
        if row.key in _KV_DEFAULTS:
            values[row.key] = row.value.get("value", _KV_DEFAULTS[row.key])
    return values


async def _upsert_kv(session: AsyncSession, key: str, value: Any) -> None:
    """Insert or update a single key in the system_settings table."""
    result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = {"value": value}
    else:
        session.add(SystemSetting(key=key, value={"value": value}))


async def _build_response(session: AsyncSession) -> SystemSettingsResponse:
    """Assemble the full settings response from KV store + SystemConfig + env."""
    kv = await _read_all_kv(session)
    config = await _get_or_create_system_config(session)

    ha_url = str(SETTINGS.home_assistant_url)
    ha_token = SETTINGS.home_assistant_token
    masked_token = f"{ha_token[:8]}...{ha_token[-4:]}" if len(ha_token) > 12 else ("***" if ha_token else "")

    return SystemSettingsResponse(
        system_name=kv["system_name"],
        current_mode=config.current_mode.value,
        timezone=kv["timezone"],
        temperature_unit=kv["temperature_unit"],
        default_comfort_temp_min=float(kv["default_comfort_temp_min"]),
        default_comfort_temp_max=float(kv["default_comfort_temp_max"]),
        default_humidity_min=float(kv["default_humidity_min"]),
        default_humidity_max=float(kv["default_humidity_max"]),
        energy_cost_per_kwh=float(kv["energy_cost_per_kwh"]),
        currency=kv["currency"],
        weather_entity=kv["weather_entity"],
        climate_entities=kv["climate_entities"],
        sensor_entities=kv["sensor_entities"],
        energy_entity=kv["energy_entity"],
        home_assistant_url=ha_url,
        home_assistant_token=masked_token,
        llm_settings=dict(config.llm_settings or {}),
        default_schedule=dict(config.default_schedule) if config.default_schedule else None,
        last_synced_at=config.last_synced_at.isoformat() if config.last_synced_at else None,
    )


# ---------------------------------------------------------------------------
# GET /settings — full settings response
# ---------------------------------------------------------------------------
@router.get("", response_model=SystemSettingsResponse)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemSettingsResponse:
    """Return all system settings."""
    return await _build_response(db)


# ---------------------------------------------------------------------------
# PUT /settings — partial update
# ---------------------------------------------------------------------------
@router.put("", response_model=SystemSettingsResponse)
async def update_settings(
    updates: SystemSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemSettingsResponse:
    """Update system settings. Only supplied fields are persisted."""
    changed = updates.model_dump(exclude_unset=True)
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update",
        )

    for key, value in changed.items():
        if key not in _KV_DEFAULTS:
            continue
        await _upsert_kv(db, key, value)

    await db.commit()
    return await _build_response(db)


# ---------------------------------------------------------------------------
# GET /settings/ha/entities — HA entity discovery
# ---------------------------------------------------------------------------
@router.get("/ha/entities", response_model=list[HAEntityInfo])
async def list_ha_entities(
    domain: Annotated[str | None, Query(description="Filter by entity domain")] = None,
) -> list[HAEntityInfo]:
    """Return Home Assistant entities, optionally filtered by domain."""
    from backend.api.dependencies import _ha_client
    from backend.integrations.ha_client import HAClientError

    if _ha_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Home Assistant client not connected",
        )

    try:
        states = await _ha_client.get_states()
    except HAClientError as exc:
        logger.error("Failed to fetch HA states: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to reach Home Assistant",
        ) from exc

    entities: list[HAEntityInfo] = []
    for entity in states:
        if domain and not entity.entity_id.startswith(f"{domain}."):
            continue
        entities.append(
            HAEntityInfo(
                entity_id=entity.entity_id,
                name=entity.attributes.get("friendly_name", entity.entity_id),
                state=entity.state,
            )
        )
    return entities


# ---------------------------------------------------------------------------
# GET /settings/llm/providers — LLM provider listing
# ---------------------------------------------------------------------------
@router.get("/llm/providers", response_model=LLMProvidersResponse)
async def get_llm_providers() -> LLMProvidersResponse:
    """Return configured LLM providers and their status."""
    from backend.integrations.llm.provider import ClimateIQLLMProvider

    env_key_map: dict[str, str] = {
        "anthropic": "CLIMATEIQ_ANTHROPIC_API_KEY",
        "openai": "CLIMATEIQ_OPENAI_API_KEY",
        "gemini": "CLIMATEIQ_GEMINI_API_KEY",
        "grok": "CLIMATEIQ_GROK_API_KEY",
        "ollama": "CLIMATEIQ_OLLAMA_URL",
        "llamacpp": "CLIMATEIQ_LLAMACPP_URL",
    }

    providers: list[LLMProviderInfo] = []
    for name in sorted(ClimateIQLLMProvider.SUPPORTED_PROVIDERS):
        env_var = env_key_map.get(name, "")
        configured = bool(os.environ.get(env_var, ""))
        providers.append(
            LLMProviderInfo(provider=name, configured=configured, models=[])
        )

    return LLMProvidersResponse(providers=providers)


# ---------------------------------------------------------------------------
# POST /settings/llm/providers/{provider}/refresh — refresh models
# ---------------------------------------------------------------------------
@router.post("/llm/providers/{provider}/refresh", response_model=LLMProviderInfo)
async def refresh_llm_provider(provider: str) -> LLMProviderInfo:
    """Refresh available models for a given LLM provider."""
    from backend.integrations.llm.provider import ClimateIQLLMProvider

    provider = provider.strip().lower()
    if provider not in ClimateIQLLMProvider.SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider. Must be one of: {sorted(ClimateIQLLMProvider.SUPPORTED_PROVIDERS)}",
        )

    env_key_map: dict[str, str] = {
        "anthropic": "CLIMATEIQ_ANTHROPIC_API_KEY",
        "openai": "CLIMATEIQ_OPENAI_API_KEY",
        "gemini": "CLIMATEIQ_GEMINI_API_KEY",
        "grok": "CLIMATEIQ_GROK_API_KEY",
        "ollama": "CLIMATEIQ_OLLAMA_URL",
        "llamacpp": "CLIMATEIQ_LLAMACPP_URL",
    }

    env_var = env_key_map.get(provider, "")
    configured = bool(os.environ.get(env_var, ""))

    return LLMProviderInfo(provider=provider, configured=configured, models=[])


# ---------------------------------------------------------------------------
# Stubs — users (kept for API compatibility)
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    id: str
    name: str
    email: str | None = None

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    name: str
    email: str | None = None


@router.get("/users")
async def list_users() -> list[UserResponse]:
    """List users."""
    return []


@router.post("/users", status_code=201)
async def create_user(user: UserCreate) -> UserResponse:
    """Create user."""
    raise HTTPException(status_code=501, detail="Not implemented")


# ---------------------------------------------------------------------------
# Stubs — integrations (kept for API compatibility)
# ---------------------------------------------------------------------------


class IntegrationTestResult(BaseModel):
    success: bool
    message: str
    latency_ms: float | None = None


@router.get("/integrations")
async def get_integrations() -> dict[str, Any]:
    """Get integrations."""
    return {"homeassistant": {}, "mqtt": {}, "weather": {}}


@router.post("/integrations/{integration}/test")
async def test_integration(integration: str) -> IntegrationTestResult:
    """Test integration."""
    return IntegrationTestResult(success=True, message="OK")


__all__ = ["router"]
