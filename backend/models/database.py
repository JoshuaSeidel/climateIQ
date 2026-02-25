"""SQLAlchemy models and async engine manager for ClimateIQ."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

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
    priority: Mapped[int] = mapped_column(Integer(), default=5)  # 1-10, higher = more important
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    comfort_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    thermal_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Zone exclusion from metrics and AI control loop.
    # When exclude_from_metrics is True, the zone is omitted from analytics
    # aggregates (overview, comfort, energy) and from the AI decision loop
    # (RuleEngine, PID, PatternEngine).  If exclude_months is non-empty,
    # the exclusion only applies during those calendar months (1-12).
    exclude_from_metrics: Mapped[bool] = mapped_column(Boolean(), default=False)
    exclude_months: Mapped[list[int]] = mapped_column(JSONB, default=list)

    sensors: Mapped[list[Sensor]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )
    devices: Mapped[list[Device]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )
    occupancy_patterns: Mapped[list[OccupancyPattern]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )

    @property
    def is_currently_excluded(self) -> bool:
        """Return True if the zone should be excluded from metrics right now.

        If ``exclude_from_metrics`` is False, always returns False.
        If ``exclude_months`` is empty, the exclusion is year-round.
        Otherwise, the exclusion only applies during the listed months.
        """
        if not self.exclude_from_metrics:
            return False
        if not self.exclude_months:
            return True  # year-round exclusion
        return datetime.now(UTC).month in self.exclude_months


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
    # DEPRECATED: use zone_ids instead. Kept for backwards compatibility with create_all().
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE")
    )
    zone_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)  # list of zone UUID strings; empty = all zones
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


class UserDirective(Base):
    """User directives / preferences extracted from chat conversations.

    These are long-term memory items that persist across sessions and are
    injected into both the chat system prompt and the AI decision loop so
    the system remembers user preferences (e.g. "never heat the basement
    above 65 F", "I prefer it cooler at night").
    """

    __tablename__ = "user_directives"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid_pk)
    directive: Mapped[str] = mapped_column(Text(), nullable=False)
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(
        String(64), default="preference"
    )  # preference, constraint, schedule_hint, comfort, energy, house_info, routine, occupancy
    is_active: Mapped[bool] = mapped_column(Boolean(), default=True)
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    zone: Mapped[Zone | None] = relationship()
    source_conversation: Mapped[Conversation | None] = relationship()


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


def get_engine() -> AsyncEngine:
    """Get the global async engine."""
    global _engine
    if _engine is None:
        from backend.config import get_settings

        settings = get_settings()

        _db_logger.info(
            "Creating engine -> %s:%s/%s",
            settings.db_host,
            settings.db_port,
            settings.db_name,
        )

        _engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            future=True,
            pool_size=5,
            max_overflow=10,
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


async def _safe_execute(conn: Any, sql: str, label: str, *, level: str = "warning") -> bool:
    """Execute *sql* inside a SAVEPOINT so a failure doesn't poison the transaction.

    Returns ``True`` on success, ``False`` on failure (logged at *level*).
    """
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql))
        return True
    except Exception as exc:
        msg = "Could not %s: %s"
        if level == "debug":
            _db_logger.debug(msg, label, exc)
        else:
            _db_logger.warning(msg, label, exc)
        return False


async def _ensure_hypertables(conn: Any) -> None:
    """Create TimescaleDB hypertables, migrating primary keys as needed.

    TimescaleDB requires the partitioning column to be part of every unique
    constraint.  The ORM models define a UUID-only PK, so we first widen it
    to a composite ``(id, <time_col>)`` PK before calling
    ``create_hypertable``.

    Each DDL runs inside its own SAVEPOINT so that a single failure does not
    poison the surrounding transaction.
    """
    for tbl, col in (("sensor_readings", "recorded_at"), ("device_actions", "created_at")):
        # Skip if the table is already a hypertable.
        try:
            async with conn.begin_nested():
                result = await conn.execute(
                    text(
                        "SELECT 1 FROM timescaledb_information.hypertables "
                        "WHERE hypertable_name = :tbl"
                    ),
                    {"tbl": tbl},
                )
                if result.scalar() is not None:
                    _db_logger.debug("Table %s is already a hypertable — skipping", tbl)
                    continue
        except Exception:
            # timescaledb_information may not exist if the extension is absent.
            _db_logger.warning(
                "Could not query timescaledb_information for %s — "
                "TimescaleDB may not be installed",
                tbl,
            )
            continue

        # Widen the primary key to include the partitioning column.
        await _safe_execute(
            conn,
            f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {tbl}_pkey",
            f"drop PK for {tbl}",
        )
        await _safe_execute(
            conn,
            f"ALTER TABLE {tbl} ADD PRIMARY KEY (id, {col})",
            f"add composite PK for {tbl}",
        )

        # Now create the hypertable.
        await _safe_execute(
            conn,
            f"SELECT create_hypertable('{tbl}', '{col}', "
            "if_not_exists => TRUE, migrate_data => TRUE)",
            f"create hypertable for {tbl}",
        )

    _db_logger.info("TimescaleDB hypertables ensured")


# Continuous-aggregate DDL and associated policies / refreshes.
# These MUST run outside a transaction (AUTOCOMMIT) because TimescaleDB
# forbids ``CREATE MATERIALIZED VIEW … WITH DATA`` inside a transaction block.

_CAGG_DDL: list[tuple[str, str]] = [
    (
        "sensor_readings_5min",
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_5min
        WITH (timescaledb.continuous) AS
        SELECT
            sensor_id,
            zone_id,
            time_bucket('5 minutes', recorded_at) AS bucket,
            avg(temperature_c) AS avg_temperature_c,
            avg(humidity) AS avg_humidity,
            avg(lux) AS avg_lux,
            bool_or(presence) AS presence
        FROM sensor_readings
        GROUP BY sensor_id, zone_id, bucket
        """,
    ),
    (
        "sensor_readings_hourly",
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_hourly
        WITH (timescaledb.continuous) AS
        SELECT
            sensor_id,
            zone_id,
            time_bucket('1 hour', recorded_at) AS bucket,
            avg(temperature_c) AS avg_temperature_c,
            avg(humidity) AS avg_humidity,
            avg(lux) AS avg_lux,
            bool_or(presence) AS presence
        FROM sensor_readings
        GROUP BY sensor_id, zone_id, bucket
        """,
    ),
    (
        "sensor_readings_daily",
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_daily
        WITH (timescaledb.continuous) AS
        SELECT
            sensor_id,
            zone_id,
            time_bucket('1 day', recorded_at) AS bucket,
            avg(temperature_c) AS avg_temperature_c,
            avg(humidity) AS avg_humidity,
            avg(lux) AS avg_lux,
            bool_or(presence) AS presence
        FROM sensor_readings
        GROUP BY sensor_id, zone_id, bucket
        """,
    ),
]

_CAGG_POLICIES: list[tuple[str, str, str, str]] = [
    ("sensor_readings_5min", "'5 minutes'", "'1 minute'", "'5 minutes'"),
    ("sensor_readings_hourly", "'3 hours'", "'1 hour'", "'1 hour'"),
    ("sensor_readings_daily", "'3 days'", "'1 day'", "'1 day'"),
]


async def _ensure_continuous_aggregates(engine: AsyncEngine) -> None:
    """Create continuous aggregates, refresh policies, and compression policies.

    Runs on a dedicated AUTOCOMMIT connection so that TimescaleDB DDL that
    cannot execute inside a transaction block succeeds.
    """
    async with engine.connect() as base_conn:
        conn = await base_conn.execution_options(isolation_level="AUTOCOMMIT")

        # --- Continuous aggregates -------------------------------------------
        for name, ddl in _CAGG_DDL:
            try:
                await conn.execute(text(ddl))
            except Exception as exc:
                _db_logger.warning("Could not create continuous aggregate %s: %s", name, exc)

        # --- Refresh policies ------------------------------------------------
        for view, start_off, end_off, interval in _CAGG_POLICIES:
            try:
                await conn.execute(
                    text(
                        f"SELECT add_continuous_aggregate_policy('{view}', "
                        f"start_offset => INTERVAL {start_off}, "
                        f"end_offset => INTERVAL {end_off}, "
                        f"schedule_interval => INTERVAL {interval}, "
                        "if_not_exists => TRUE)"
                    )
                )
            except Exception as exc:
                _db_logger.debug("Could not add refresh policy for %s: %s", view, exc)

        # --- Manual refresh so data is available immediately -----------------
        for view in ("sensor_readings_5min", "sensor_readings_hourly", "sensor_readings_daily"):
            try:
                await conn.execute(
                    text(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
                )
            except Exception as exc:
                _db_logger.debug("Could not refresh %s: %s", view, exc)

        # --- Compression policies --------------------------------------------
        for tbl in ("sensor_readings", "device_actions"):
            try:
                await conn.execute(
                    text(
                        f"SELECT add_compression_policy('{tbl}', "
                        "INTERVAL '30 days', if_not_exists => TRUE)"
                    )
                )
            except Exception as exc:
                _db_logger.debug("Could not add compression policy for %s: %s", tbl, exc)

    _db_logger.info("TimescaleDB continuous aggregates ensured")


async def init_db() -> None:
    """Initialize database - create all tables."""
    engine = get_engine()
    async with engine.begin() as conn:
        # Ensure required PostgreSQL extensions exist before creating tables.
        # These may fail if the DB user isn't a superuser — that's OK as long
        # as the extensions were pre-created by an admin.
        for ext in ("uuid-ossp", "vector"):
            try:
                await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
            except Exception:
                _db_logger.warning(
                    "Could not create extension '%s' (needs superuser). "
                    "Ensure it is pre-installed on the database.", ext,
                )
        await conn.run_sync(Base.metadata.create_all)

        # --- Migration: add zone_ids column to schedules if missing ----------
        # create_all() won't add columns to existing tables, so we do it manually.
        try:
            await conn.execute(text(
                "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS zone_ids JSONB DEFAULT '[]'::jsonb"
            ))
        except Exception:
            _db_logger.warning("Could not add zone_ids column to schedules")

        # Migrate legacy zone_id -> zone_ids for rows that haven't been migrated yet
        try:
            await conn.execute(text("""
                UPDATE schedules
                SET zone_ids = CASE
                    WHEN zone_id IS NOT NULL THEN jsonb_build_array(zone_id::text)
                    ELSE '[]'::jsonb
                END
                WHERE zone_ids IS NULL OR zone_ids = 'null'::jsonb
            """))
        except Exception:
            _db_logger.warning("Could not migrate schedule zone_id -> zone_ids")

        # --- Migration: add zone exclusion columns if missing ----------------
        try:
            await conn.execute(text(
                "ALTER TABLE zones "
                "ADD COLUMN IF NOT EXISTS exclude_from_metrics BOOLEAN DEFAULT FALSE"
            ))
            await conn.execute(text(
                "ALTER TABLE zones "
                "ADD COLUMN IF NOT EXISTS exclude_months JSONB DEFAULT '[]'::jsonb"
            ))
        except Exception:
            _db_logger.warning("Could not add zone exclusion columns")

        # --- Migration: add zone priority column if missing ------------------
        try:
            await conn.execute(text(
                "ALTER TABLE zones "
                "ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 5"
            ))
        except Exception:
            _db_logger.warning("Could not add zone priority column")

        # --- TimescaleDB hypertables (runs inside the transaction) -----------
        await _ensure_hypertables(conn)

    # --- TimescaleDB continuous aggregates (requires AUTOCOMMIT) ---------
    # Must run AFTER the transaction block above has committed so that the
    # hypertables exist before we create aggregates on top of them.
    await _ensure_continuous_aggregates(engine)


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
