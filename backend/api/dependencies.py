"""FastAPI dependency injection helpers."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import SETTINGS, Settings
from backend.integrations import HAClient
from backend.models.database import get_session_maker

# ---------------------------------------------------------------------------
# Settings dependency
# ---------------------------------------------------------------------------


def get_settings_dependency() -> Settings:
    return SETTINGS


type SettingsDep = Annotated[Settings, Depends(get_settings_dependency)]


# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield a single transactional async SQLAlchemy session."""

    session_maker = get_session_maker()
    async with session_maker() as session:
        yield session


# ---------------------------------------------------------------------------
# Redis dependency
# ---------------------------------------------------------------------------


_shared_redis: redis.Redis | None = None


def set_shared_redis(client: redis.Redis | None) -> None:
    """Set the shared Redis client (called during app startup)."""
    global _shared_redis
    _shared_redis = client


async def get_redis() -> AsyncGenerator[redis.Redis]:
    """Yield the shared Redis client, falling back to a new one if needed."""
    if _shared_redis is not None:
        yield _shared_redis
    else:
        client = redis.from_url(
            str(SETTINGS.redis_url),
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
        try:
            yield client
        finally:
            await client.aclose()


type RedisDep = Annotated[redis.Redis, Depends(get_redis)]


# ---------------------------------------------------------------------------
# Home Assistant client dependency
# ---------------------------------------------------------------------------


_ha_client: HAClient | None = None


async def get_ha_client(settings: SettingsDep) -> HAClient:
    global _ha_client
    if _ha_client is None and settings.home_assistant_token:
        client = HAClient(
            url=str(settings.home_assistant_url),
            token=settings.home_assistant_token,
        )
        await client.connect()
        _ha_client = client
    if _ha_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Home Assistant token not configured",
        )
    return _ha_client


type HADep = Annotated[HAClient, Depends(get_ha_client)]


__all__ = [
    "HADep",
    "RedisDep",
    "SettingsDep",
    "get_db",
    "get_ha_client",
    "get_redis",
    "set_shared_redis",
]
