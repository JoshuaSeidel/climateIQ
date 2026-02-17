"""Application configuration powered by Pydantic settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Final
from urllib.parse import quote_plus

from pydantic import AnyUrl, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _generate_secret() -> str:
    import secrets

    return secrets.token_urlsafe(32)


class Settings(BaseSettings):
    """Centralized configuration object with environment fallbacks."""

    model_config = SettingsConfigDict(env_prefix="CLIMATEIQ_", env_file=".env", extra="allow")

    # App
    app_name: str = "ClimateIQ"
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8420
    secret_key: str = Field(default_factory=_generate_secret)
    debug: bool = False

    @field_validator("secret_key", mode="before")
    @classmethod
    def _coerce_empty_secret(cls, v: str) -> str:
        """Treat empty string (e.g. env var set but blank) as unset so the
        default factory generates a secure random key."""
        if isinstance(v, str) and not v.strip():
            return _generate_secret()
        return v

    # Database
    db_host: str = Field(default="localhost")
    db_port: int = Field(default=5432)
    db_name: str = Field(default="climateiq")
    db_user: str = Field(default="climateiq")
    db_password: str = Field(default="climateiq")
    db_url: AnyUrl | str | None = Field(default=None)
    db_ssl: bool = Field(default=False)

    # Redis
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_url: AnyUrl | str = Field(default="redis://localhost:6379/0")

    # Home Assistant
    home_assistant_url: AnyUrl | str = Field(default="http://localhost:8123")
    home_assistant_token: str = Field(default="")

    # Home Assistant Add-on mode
    ha_addon_mode: bool = Field(default=False)
    temperature_unit: str = Field(default="F")
    log_level: str = Field(default="info")

    # Entity filters (comma-separated entity IDs; empty = no filter / all entities)
    climate_entities: str = Field(default="")
    sensor_entities: str = Field(default="")
    weather_entity: str = Field(default="")
    energy_entity: str = Field(default="")

    # Safety limits (absolute bounds, not user-configurable via env for safety)
    safety_min_temp_c: float = Field(default=4.4)  # 40°F - freeze protection
    safety_max_temp_c: float = Field(default=37.8)  # 100°F - overheat protection
    safety_min_temp_f: float = Field(default=40.0)
    safety_max_temp_f: float = Field(default=100.0)

    # Comfort defaults (used when schedule has no explicit setpoints)
    default_comfort_temp_min_c: float = 20.0
    default_comfort_temp_max_c: float = 24.0

    # LLM providers
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    grok_api_key: str = Field(default="")
    ollama_url: AnyUrl | str = Field(default="http://localhost:11434")
    llamacpp_url: AnyUrl | str = Field(default="http://localhost:8000")

    # API key authentication (empty = no auth required)
    api_key: str = Field(default="")

    # GitHub
    github_repo: str = Field(default="climateiq/backend")
    github_token: str = Field(default="")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Return a fully qualified async SQLAlchemy database URL."""

        if self.db_url:
            return str(self.db_url)
        return (
            f"postgresql+psycopg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_provider_config(self) -> dict[str, dict[str, str | bool]]:
        """Expose configured LLM provider metadata."""

        return {
            "anthropic": {
                "api_key": self.anthropic_api_key,
                "configured": bool(self.anthropic_api_key),
            },
            "openai": {
                "api_key": self.openai_api_key,
                "configured": bool(self.openai_api_key),
            },
            "gemini": {
                "api_key": self.gemini_api_key,
                "configured": bool(self.gemini_api_key),
            },
            "grok": {
                "api_key": self.grok_api_key,
                "configured": bool(self.grok_api_key),
            },
            "ollama": {
                "base_url": str(self.ollama_url),
                "configured": False,
            },
            "llamacpp": {
                "base_url": str(self.llamacpp_url),
                "configured": False,
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()


SETTINGS: Final[Settings] = get_settings()
