"""Initial ClimateIQ schema."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


zone_type_enum = sa.Enum(
    "bedroom",
    "living_area",
    "kitchen",
    "bathroom",
    "hallway",
    "basement",
    "attic",
    "garage",
    "office",
    "other",
    name="zone_type_enum",
    native_enum=False,
)

sensor_type_enum = sa.Enum(
    "multisensor",
    "temp_only",
    "humidity_only",
    "presence_only",
    "temp_humidity",
    "presence_lux",
    "other",
    name="sensor_type_enum",
    native_enum=False,
)

device_type_enum = sa.Enum(
    "thermostat",
    "smart_vent",
    "blind",
    "shade",
    "space_heater",
    "fan",
    "mini_split",
    "humidifier",
    "dehumidifier",
    "other",
    name="device_type_enum",
    native_enum=False,
)

control_method_enum = sa.Enum(
    "ha_service_call",
    "mqtt_direct",
    name="control_method_enum",
    native_enum=False,
)

trigger_type_enum = sa.Enum(
    "schedule",
    "llm_decision",
    "user_override",
    "follow_me",
    "comfort_correction",
    "rule_engine",
    "anomaly_response",
    name="trigger_type_enum",
    native_enum=False,
)

action_type_enum = sa.Enum(
    "set_temperature",
    "set_vent_position",
    "set_mode",
    "open_cover",
    "close_cover",
    "set_cover_position",
    "turn_on",
    "turn_off",
    "set_fan_speed",
    name="action_type_enum",
    native_enum=False,
)

pattern_type_enum = sa.Enum(
    "weekday",
    "weekend",
    "holiday",
    name="pattern_type_enum",
    native_enum=False,
)

season_enum = sa.Enum(
    "spring",
    "summer",
    "fall",
    "winter",
    name="season_enum",
    native_enum=False,
)

feedback_type_enum = sa.Enum(
    "too_hot",
    "too_cold",
    "too_humid",
    "too_dry",
    "comfortable",
    "schedule_change",
    "preference",
    "other",
    name="feedback_type_enum",
    native_enum=False,
)

system_mode_enum = sa.Enum(
    "learn",
    "scheduled",
    "follow_me",
    "active",
    name="system_mode_enum",
    native_enum=False,
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgvector")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    bind = op.get_bind()
    for enum in (
        zone_type_enum,
        sensor_type_enum,
        device_type_enum,
        control_method_enum,
        trigger_type_enum,
        action_type_enum,
        pattern_type_enum,
        season_enum,
        feedback_type_enum,
        system_mode_enum,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", zone_type_enum, nullable=False),
        sa.Column("floor", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "comfort_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "thermal_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "sensors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sensor_type_enum, nullable=False),
        sa.Column("manufacturer", sa.String(128)),
        sa.Column("model", sa.String(128)),
        sa.Column("firmware_version", sa.String(64)),
        sa.Column("ha_entity_id", sa.String(255)),
        sa.Column("entity_id", sa.String(255)),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "calibration_offsets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", device_type_enum, nullable=False),
        sa.Column("manufacturer", sa.String(128)),
        sa.Column("model", sa.String(128)),
        sa.Column("ha_entity_id", sa.String(255)),
        sa.Column("control_method", control_method_enum, nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "sensor_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sensor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("temperature_c", sa.Float()),
        sa.Column("humidity", sa.Float()),
        sa.Column("presence", sa.Boolean()),
        sa.Column("lux", sa.Float()),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_sensor_readings_recorded_at", "sensor_readings", ["recorded_at"])

    op.create_table(
        "device_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column("triggered_by", trigger_type_enum, nullable=False),
        sa.Column("action_type", action_type_enum, nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("reasoning", sa.Text()),
        sa.Column("mode", system_mode_enum),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_device_actions_created_at", "device_actions", ["created_at"])

    op.create_table(
        "occupancy_patterns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pattern_type", pattern_type_enum, nullable=False),
        sa.Column("season", season_enum, nullable=False),
        sa.Column("schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_response", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_conversations_session_id", "conversations", ["session_id"])

    op.create_table(
        "user_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column("feedback_type", feedback_type_enum, nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="SET NULL"),
    )

    op.execute("ALTER TABLE user_feedback ADD COLUMN IF NOT EXISTS embedding vector(1536)")

    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("current_mode", system_mode_enum, nullable=False),
        sa.Column("default_schedule", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "llm_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "days_of_week",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[0,1,2,3,4,5,6]'::jsonb"),
        ),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=True),
        sa.Column("target_temp_c", sa.Float(), nullable=False),
        sa.Column("hvac_mode", sa.String(20), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("key", name="uq_system_settings_key"),
    )
    op.create_index("ix_system_settings_key", "system_settings", ["key"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # TimescaleDB hypertables
    op.execute(
        "SELECT create_hypertable('sensor_readings', 'recorded_at', if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT create_hypertable('device_actions', 'created_at', if_not_exists => TRUE, migrate_data => TRUE)"
    )

    # Continuous aggregates
    op.execute(
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
        """
    )
    op.execute(
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
        """
    )
    op.execute(
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
        """
    )

    # Compression policies
    op.execute("SELECT add_compression_policy('sensor_readings', INTERVAL '30 days')")
    op.execute("SELECT add_compression_policy('device_actions', INTERVAL '30 days')")

    op.execute(
        sa.text(
            """
            INSERT INTO system_config (id, current_mode, default_schedule, llm_settings, last_synced_at)
            VALUES (:id, :mode, :default_schedule, :llm_settings, :last_synced_at)
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(
            id=1,
            mode="learn",
            default_schedule=None,
            llm_settings={},
            last_synced_at=datetime.now(UTC),
        )
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('sensor_readings', if_exists => TRUE)")
    op.execute("SELECT remove_compression_policy('device_actions', if_exists => TRUE)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS sensor_readings_daily")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS sensor_readings_hourly")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS sensor_readings_5min")
    op.drop_table("users")
    op.drop_index("ix_system_settings_key", table_name="system_settings")
    op.drop_table("system_settings")
    op.drop_table("schedules")
    op.drop_table("system_config")
    op.drop_table("user_feedback")
    op.drop_index("ix_conversations_session_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("occupancy_patterns")
    op.drop_index("ix_device_actions_created_at", table_name="device_actions")
    op.drop_table("device_actions")
    op.drop_index("ix_sensor_readings_recorded_at", table_name="sensor_readings")
    op.drop_table("sensor_readings")
    op.drop_table("devices")
    op.drop_table("sensors")
    op.drop_table("zones")

    bind = op.get_bind()
    for enum in (
        system_mode_enum,
        feedback_type_enum,
        season_enum,
        pattern_type_enum,
        action_type_enum,
        trigger_type_enum,
        control_method_enum,
        device_type_enum,
        sensor_type_enum,
        zone_type_enum,
    ):
        enum.drop(bind, checkfirst=True)

    op.execute("DROP EXTENSION IF EXISTS timescaledb")
    op.execute("DROP EXTENSION IF EXISTS pgvector")
