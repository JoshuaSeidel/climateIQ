"""Add performance indexes for sensor_readings, sensors, and devices.

sensor_readings(sensor_id, recorded_at DESC) — covers the most-queried
pattern: "latest reading for a set of sensors".  Without this index every
zone-list request does a full table scan on the largest table.

sensors(zone_id) and devices(zone_id) — FK lookups used in every zone
enrichment cycle.

Revision ID: 002_perf_indexes
Revises: 001_initial_schema
Create Date: 2026-02-24
"""

from alembic import op

revision = "002_perf_indexes"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Compound covering index: latest-reading-per-sensor queries
    op.create_index(
        "idx_sensor_readings_sensor_id_recorded_at",
        "sensor_readings",
        ["sensor_id", "recorded_at"],
        postgresql_ops={"recorded_at": "DESC"},
    )

    # FK indexes on sensors and devices (zone_id lookups)
    op.create_index("idx_sensors_zone_id", "sensors", ["zone_id"])
    op.create_index("idx_devices_zone_id", "devices", ["zone_id"])

    # Partial index: active-zone queries used in every background task
    op.create_index(
        "idx_zones_is_active",
        "zones",
        ["is_active"],
        postgresql_where="is_active = true",
    )

    # Partial index: enabled-schedule lookups
    op.create_index(
        "idx_schedules_is_enabled",
        "schedules",
        ["is_enabled"],
        postgresql_where="is_enabled = true",
    )


def downgrade() -> None:
    op.drop_index("idx_schedules_is_enabled", table_name="schedules")
    op.drop_index("idx_zones_is_active", table_name="zones")
    op.drop_index("idx_devices_zone_id", table_name="devices")
    op.drop_index("idx_sensors_zone_id", table_name="sensors")
    op.drop_index(
        "idx_sensor_readings_sensor_id_recorded_at",
        table_name="sensor_readings",
    )
