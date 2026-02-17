"""SQLAlchemy models and async engine manager for ClimateIQ."""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.models.enums import (
    ActionType,
    ControlMethod,
    DeviceType,
    FeedbackType,
    PatternType,
    Season,
    SensorType,
    SystemMode,
    TriggerType,
    ZoneType,
)


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all ORM models."""


def uuid_pk() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(UTC)


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    type: Mapped[ZoneType] = mapped_column(
        SQLEnum(ZoneType, name="zone_type_enum", native_enum=False), nullable=False
    )
    floor: Mapped[int | None] = mapped_column(Integer())
    is_active: Mapped[bool] = mapped_column(Boolean(), default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    comfort_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    thermal_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    sensors: Mapped[list[Sensor]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )
    devices: Mapped[list[Device]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )
    occupancy_patterns: Mapped[list[OccupancyPattern]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )


class Sensor(Base):
    __tablename__ = "sensors"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[SensorType] = mapped_column(
        SQLEnum(SensorType, name="sensor_type_enum", native_enum=False), nullable=False
    )
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    firmware_version: Mapped[str | None] = mapped_column(String(64))
    ha_entity_id: Mapped[str | None] = mapped_column(String(255))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    calibration_offsets: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean(), default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    zone: Mapped[Zone] = relationship(back_populates="sensors")
    readings: Mapped[list[SensorReading]] = relationship(
        back_populates="sensor", cascade="all, delete-orphan"
    )


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[DeviceType] = mapped_column(
        SQLEnum(DeviceType, name="device_type_enum", native_enum=False), nullable=False
    )
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    ha_entity_id: Mapped[str | None] = mapped_column(String(255))
    control_method: Mapped[ControlMethod] = mapped_column(
        SQLEnum(ControlMethod, name="control_method_enum", native_enum=False), nullable=False
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_primary: Mapped[bool] = mapped_column(Boolean(), default=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    zone: Mapped[Zone] = relationship(back_populates="devices")
    actions: Mapped[list[DeviceAction]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    sensor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    temperature_c: Mapped[float | None] = mapped_column(Float())
    humidity: Mapped[float | None] = mapped_column(Float())
    presence: Mapped[bool | None] = mapped_column(Boolean())
    lux: Mapped[float | None] = mapped_column(Float())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    sensor: Mapped[Sensor] = relationship(back_populates="readings")


class DeviceAction(Base):
    __tablename__ = "device_actions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    device_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL")
    )
    triggered_by: Mapped[TriggerType] = mapped_column(
        SQLEnum(TriggerType, name="trigger_type_enum", native_enum=False), nullable=False
    )
    action_type: Mapped[ActionType] = mapped_column(
        SQLEnum(ActionType, name="action_type_enum", native_enum=False), nullable=False
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reasoning: Mapped[str | None] = mapped_column(Text())
    mode: Mapped[SystemMode | None] = mapped_column(
        SQLEnum(SystemMode, name="system_mode_enum", native_enum=False, create_constraint=False)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    device: Mapped[Device] = relationship(back_populates="actions")


class OccupancyPattern(Base):
    __tablename__ = "occupancy_patterns"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False
    )
    pattern_type: Mapped[PatternType] = mapped_column(
        SQLEnum(PatternType, name="pattern_type_enum", native_enum=False), nullable=False
    )
    season: Mapped[Season] = mapped_column(
        SQLEnum(Season, name="season_enum", native_enum=False), nullable=False
    )
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    zone: Mapped[Zone] = relationship(back_populates="occupancy_patterns")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_message: Mapped[str] = mapped_column(Text(), nullable=False)
    assistant_response: Mapped[str] = mapped_column(Text(), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def meta(self) -> dict[str, Any]:
        return self.metadata_

    @meta.setter
    def meta(self, value: dict[str, Any]) -> None:
        self.metadata_ = value


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL")
    )
    feedback_type: Mapped[FeedbackType] = mapped_column(
        SQLEnum(FeedbackType, name="feedback_type_enum", native_enum=False), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text())
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    zone: Mapped[Zone | None] = relationship()

    @property
    def meta(self) -> dict[str, Any]:
        return self.metadata_

    @meta.setter
    def meta(self, value: dict[str, Any]) -> None:
        self.metadata_ = value


class SystemConfig(Base):
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    current_mode: Mapped[SystemMode] = mapped_column(
        SQLEnum(SystemMode, name="system_mode_enum", native_enum=False), nullable=False
    )
    default_schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    llm_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Schedule(Base):
    """Schedule for automated zone control."""

    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE")
    )
    days_of_week: Mapped[list[int]] = mapped_column(JSONB, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # HH:MM
    end_time: Mapped[str | None] = mapped_column(String(5))  # HH:MM
    target_temp_c: Mapped[float] = mapped_column(Float(), nullable=False)
    hvac_mode: Mapped[str] = mapped_column(String(20), default="auto")
    is_enabled: Mapped[bool] = mapped_column(Boolean(), default=True)
    priority: Mapped[int] = mapped_column(Integer(), default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    zone: Mapped[Zone | None] = relationship()


class SystemSetting(Base):
    """Key-value storage for system settings."""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Base):
    """User accounts for multi-user support."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), unique=True)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AsyncEngineManager:
    """Backwards-compatible engine manager (thin wrapper)."""

    def __init__(self, database_url: str, echo: bool = False) -> None:
        self._engine = create_async_engine(database_url, echo=echo, future=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def create_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session


# ============================================================================
# Global engine and session management
# ============================================================================

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


_db_logger = logging.getLogger(__name__)


def _pre_resolve_url(database_url: str) -> str:
    """Replace the hostname in *database_url* with an already-resolved IP.

    On Alpine/musl, glibc-style ``getaddrinfo`` is not fully thread-safe.
    ``asyncio``'s default loop calls ``getaddrinfo`` inside
    ``run_in_executor`` (a thread-pool), which can trigger
    ``[Errno -2] Name does not resolve`` even for bare IP addresses.

    By resolving the host once in the main thread (before the event loop
    hands the URL to asyncpg) we guarantee that asyncpg never needs to call
    ``getaddrinfo`` at all.
    """
    parsed = urlparse(database_url)
    host = parsed.hostname
    if not host:
        return database_url

    try:
        # Prefer IPv4 (AF_INET) to avoid IPv6 bracket-escaping issues in URLs.
        # Fall back to any address family if IPv4 isn't available.
        for family in (socket.AF_INET, 0):
            try:
                infos = socket.getaddrinfo(
                    host, parsed.port or 5432, family=family, type=socket.SOCK_STREAM,
                )
                if infos:
                    break
            except socket.gaierror:
                if family == 0:
                    raise
                continue
        else:
            return database_url

        resolved_ip = str(infos[0][4][0])
        addr_family = infos[0][0]
        _db_logger.info(
            "Pre-resolved DB host %s -> %s (bypassing in-loop DNS)", host, resolved_ip
        )

        # IPv6 literals must be wrapped in brackets inside a URL.
        ip_for_url = f"[{resolved_ip}]" if addr_family == socket.AF_INET6 else resolved_ip

        # Rebuild the netloc.  The original host in the netloc may already be
        # bracket-wrapped (e.g. ``[::1]``), so try both forms.
        original_netloc = parsed.netloc
        for old_host in (f"[{host}]", str(host)):
            if old_host in original_netloc:
                netloc = original_netloc.replace(old_host, ip_for_url, 1)
                return urlunparse(parsed._replace(netloc=netloc))

        # Fallback: couldn't locate the host token in netloc — return unchanged.
    except Exception as exc:
        _db_logger.warning("Pre-resolution of DB host %s failed (%s); using original URL", host, exc)

    return database_url


def get_engine() -> AsyncEngine:
    """Get the global async engine."""
    global _engine
    if _engine is None:
        from backend.config import get_settings

        settings = get_settings()

        # Pre-resolve the hostname so asyncpg/asyncio never calls
        # getaddrinfo inside the thread-pool (broken on Alpine musl).
        db_url = _pre_resolve_url(settings.database_url)

        # Build connect_args to pass directly to asyncpg.
        # ssl=False bypasses URL parsing issues on musl.
        connect_args: dict[str, object] = {}
        if not settings.db_ssl:
            connect_args["ssl"] = False

        _engine = create_async_engine(
            db_url,
            echo=settings.debug,
            future=True,
            pool_size=5,
            max_overflow=10,
            connect_args=connect_args,
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Get the global session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


# Alias for compatibility (callable for legacy imports)
def async_session_maker() -> async_sessionmaker[AsyncSession]:
    return get_session_maker()


async def init_db() -> None:
    """Initialize database - create all tables."""
    engine = get_engine()
    async with engine.begin() as conn:
        # Ensure required PostgreSQL extensions exist before creating tables.
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
