"""Backup and restore service for ClimateIQ.

Creates JSON-based backups of the database state and supports
listing, restoring, and deleting backups.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_DEFAULT_BACKUP_DIR = Path(__file__).resolve().parents[2] / "backups"

# Tables to back up, in dependency order (parents before children).
_BACKUP_TABLES = [
    "zones",
    "sensors",
    "devices",
    "sensor_readings",
    "device_actions",
    "occupancy_patterns",
    "conversations",
    "user_directives",
    "user_feedback",
    "system_config",
    "schedules",
    "system_settings",
    "users",
]

_ALLOWED_TABLES = frozenset(_BACKUP_TABLES)


def _validate_table_name(table_name: str) -> str:
    """Validate table name against the allowlist to prevent SQL injection."""
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not in the backup allowlist")
    # Additional safety: ensure no special characters
    if not table_name.replace("_", "").isalnum():
        raise ValueError(f"Table name '{table_name}' contains invalid characters")
    return table_name


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BackupInfo:
    """Metadata about a stored backup."""

    backup_id: str
    filename: str
    created_at: str
    size_bytes: int
    table_counts: dict[str, int]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class BackupService:
    """Create, list, restore, and delete JSON database backups.

    Backups are stored as JSON files in the configured backup directory
    (default ``/app/backups/``).  Each file contains a full snapshot of
    all application tables.

    Usage::

        service = BackupService()
        backup_id = await service.create_backup(db_session)
        backups = await service.list_backups()
        await service.restore_backup(db_session, backup_id)
        await service.delete_backup(backup_id)
    """

    def __init__(self, backup_dir: str | Path | None = None) -> None:
        self._backup_dir = Path(backup_dir) if backup_dir else _DEFAULT_BACKUP_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_backup(self, db_session: AsyncSession) -> str:
        """Dump all application tables to a JSON file.

        Args:
            db_session: An active async SQLAlchemy session.

        Returns:
            The ``backup_id`` (UUID string) of the created backup.
        """
        backup_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        timestamp_str = now.strftime("%Y%m%dT%H%M%SZ")
        filename = f"climateiq_backup_{timestamp_str}_{backup_id[:8]}.json"

        self._ensure_backup_dir()

        table_data: dict[str, list[dict[str, Any]]] = {}
        table_counts: dict[str, int] = {}

        for table_name in _BACKUP_TABLES:
            try:
                rows = await self._dump_table(db_session, table_name)
                table_data[table_name] = rows
                table_counts[table_name] = len(rows)
                logger.debug("Backed up %d rows from %s", len(rows), table_name)
            except Exception:
                logger.warning("Skipping table %s during backup (may not exist)", table_name)
                table_data[table_name] = []
                table_counts[table_name] = 0

        backup_payload = {
            "backup_id": backup_id,
            "created_at": now.isoformat(),
            "version": "1.0",
            "tables": table_data,
            "table_counts": table_counts,
        }

        filepath = self._backup_dir / filename
        filepath.write_text(
            json.dumps(backup_payload, default=_json_serializer, indent=2),
            encoding="utf-8",
        )

        size_bytes = filepath.stat().st_size
        logger.info(
            "Backup %s created (%s, %d bytes, %d tables)",
            backup_id,
            filename,
            size_bytes,
            len(table_counts),
        )
        return backup_id

    async def restore_backup(self, db_session: AsyncSession, backup_id: str) -> None:
        """Restore the database from a previously created backup.

        This performs a **destructive** restore: existing rows in each
        backed-up table are deleted before the backup data is inserted.

        Args:
            db_session: An active async SQLAlchemy session.
            backup_id: The UUID string returned by ``create_backup``.

        Raises:
            FileNotFoundError: If no backup with the given ID exists.
            ValueError: If the backup file is malformed.
        """
        filepath = self._find_backup_file(backup_id)
        if filepath is None:
            raise FileNotFoundError(f"No backup found with id {backup_id}")

        try:
            raw = filepath.read_text(encoding="utf-8")
            backup_payload = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Backup file is corrupt or unreadable: {exc}") from exc

        if not isinstance(backup_payload, dict) or not isinstance(
            backup_payload.get("tables", {}), dict
        ):
            raise ValueError("Backup file has an unexpected structure")
        tables: dict[str, list[dict[str, Any]]] = backup_payload.get("tables", {})
        if not tables:
            raise ValueError("Backup contains no table data")
        # Reject a corrupt/hostile file BEFORE anything is deleted.
        _validate_restore_rows(tables)

        # Restore in reverse dependency order (children first for deletes,
        # then parents first for inserts).
        delete_order = list(reversed(_BACKUP_TABLES))

        logger.info("Restoring backup %s — clearing existing data", backup_id)

        # The whole restore is one transaction: any failure rolls back to
        # the data as it was.  (Previously per-row errors were swallowed
        # after the DELETEs, so a restore could commit emptied tables and
        # still report success.)
        total_restored = 0
        try:
            # Phase 1: delete existing rows (children → parents)
            for table_name in delete_order:
                # An empty system_config would delete the live singleton (current
                # mode, last sync) and refill nothing: keep the live row instead.
                if table_name == "system_config" and not tables.get(table_name):
                    continue
                if table_name in tables:
                    safe_name = _validate_table_name(table_name)
                    await db_session.execute(text(f'DELETE FROM "{safe_name}"'))  # noqa: S608
                    logger.debug("Cleared table %s", table_name)

            # Phase 2: insert backup rows (parents → children)
            for table_name in _BACKUP_TABLES:
                rows = tables.get(table_name, [])
                if not rows:
                    continue
                count = await self._restore_table(db_session, table_name, rows)
                total_restored += count
                logger.debug("Restored %d rows into %s", count, table_name)

            await db_session.commit()
        except Exception as exc:
            await db_session.rollback()
            logger.exception("Backup restore failed — database left unchanged")
            raise ValueError(f"Restore failed, database left unchanged: {exc}") from exc
        logger.info(
            "Backup %s restored successfully (%d total rows across %d tables)",
            backup_id,
            total_restored,
            len(tables),
        )

    async def list_backups(self) -> list[BackupInfo]:
        """Return metadata for all available backups, newest first."""
        self._ensure_backup_dir()

        backups: list[BackupInfo] = []
        for filepath in sorted(self._backup_dir.glob("climateiq_backup_*.json"), reverse=True):
            try:
                raw = filepath.read_text(encoding="utf-8")
                data = json.loads(raw)
                backups.append(
                    BackupInfo(
                        backup_id=data.get("backup_id", filepath.stem),
                        filename=filepath.name,
                        created_at=data.get("created_at", ""),
                        size_bytes=filepath.stat().st_size,
                        table_counts=data.get("table_counts", {}),
                    )
                )
            except (json.JSONDecodeError, OSError, KeyError):
                logger.warning("Skipping unreadable backup file: %s", filepath.name)

        logger.debug("Found %d backups", len(backups))
        return backups

    async def delete_backup(self, backup_id: str) -> None:
        """Delete a backup file by its ID.

        Raises:
            FileNotFoundError: If no backup with the given ID exists.
        """
        filepath = self._find_backup_file(backup_id)
        if filepath is None:
            raise FileNotFoundError(f"No backup found with id {backup_id}")

        filepath.unlink()
        logger.info("Deleted backup %s (%s)", backup_id, filepath.name)

    def get_backup_dir(self) -> Path:
        """Return the backup directory, ensuring it exists."""
        self._ensure_backup_dir()
        return self._backup_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_backup_dir(self) -> None:
        """Create the backup directory if it doesn't exist."""
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def _find_backup_file(self, backup_id: str) -> Path | None:
        """Locate the backup file matching *backup_id*."""
        self._ensure_backup_dir()

        # Fast path: check if the ID appears in a filename
        for filepath in self._backup_dir.glob("climateiq_backup_*.json"):
            if backup_id[:8] in filepath.name:
                # Verify the full ID inside the file
                try:
                    raw = filepath.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    if data.get("backup_id") == backup_id:
                        return filepath
                except (json.JSONDecodeError, OSError):
                    continue

        # Slow path: scan all files
        for filepath in self._backup_dir.glob("climateiq_backup_*.json"):
            try:
                raw = filepath.read_text(encoding="utf-8")
                data = json.loads(raw)
                if data.get("backup_id") == backup_id:
                    return filepath
            except (json.JSONDecodeError, OSError):
                continue

        return None

    @staticmethod
    async def _dump_table(
        db_session: AsyncSession,
        table_name: str,
    ) -> list[dict[str, Any]]:
        """Fetch all rows from *table_name* as a list of dicts."""
        safe_name = _validate_table_name(table_name)
        result = await db_session.execute(text(f'SELECT * FROM "{safe_name}"'))  # noqa: S608
        columns = list(result.keys())
        rows: list[dict[str, Any]] = []
        for row in result.fetchall():
            rows.append({col: _json_safe(row[i]) for i, col in enumerate(columns)})
        return rows

    @staticmethod
    async def _restore_table(
        db_session: AsyncSession,
        table_name: str,
        rows: list[dict[str, Any]],
    ) -> int:
        """Insert *rows* into *table_name* through the ORM table definition.

        Going through the typed ``Table`` lets SQLAlchemy bind JSONB dicts,
        arrays, UUIDs and timestamps correctly — raw ``text()`` inserts with
        plain dicts/lists cannot be adapted by psycopg3.  Values that were
        stringified on export (UUIDs, timestamps, vectors) are parsed back,
        and columns that no longer exist in the schema are dropped.  Errors
        propagate so the caller can roll back the whole restore.
        """
        if not rows:
            return 0

        from sqlalchemy import insert as sa_insert

        from backend.models.database import Base

        safe_name = _validate_table_name(table_name)
        table = Base.metadata.tables.get(safe_name)
        if table is None:
            logger.warning("Skipping restore of %s — not in the current schema", safe_name)
            return 0

        coercers = {c.name: _coercer_for(c.type) for c in table.columns}
        prepared = [
            {k: coercers[k](v) for k, v in row.items() if k in coercers}
            for row in rows
        ]
        chunk = 1000
        for i in range(0, len(prepared), chunk):
            await db_session.execute(sa_insert(table), prepared[i : i + chunk])
        return len(prepared)


def _validate_restore_rows(tables: dict[str, Any]) -> None:
    """Validate rows whose bad values would drive the thermostat.

    Only what a restore must not trust: schedule targets/times/priority (a 500 °C
    target would drive the thermostat to the safety ceiling).  A backup made by
    this app always passes — the schedule routes enforce the same limits.
    """
    from backend.api.routes.schedule import parse_time
    from backend.config import SETTINGS

    lo, hi = SETTINGS.safety_min_temp_c, SETTINGS.safety_max_temp_c
    for row in tables.get("schedules") or []:
        name = row.get("name", "?") if isinstance(row, dict) else "?"
        if not isinstance(row, dict):
            raise ValueError("Backup contains a malformed schedule row")
        if "target_temp_c" in row:
            # (A row without the column fails the real insert, atomically.)
            try:
                target_f = float(row["target_temp_c"])
                if target_f != target_f:  # NaN
                    raise ValueError("NaN target")
            except (TypeError, ValueError):
                raise ValueError(
                    f"Backup schedule '{name}' has no valid target temperature"
                ) from None
            if not lo <= target_f <= hi:
                raise ValueError(
                    f"Backup schedule '{name}' has target {target_f:g} °C outside the "
                    f"safety range {lo:g}-{hi:g} °C"
                )
        for key in ("start_time", "end_time"):
            if row.get(key) is not None:
                try:
                    parse_time(str(row[key]))
                except (ValueError, TypeError):
                    raise ValueError(f"Backup schedule '{name}' has an invalid {key}") from None
        prio = row.get("priority")
        if prio is not None and (
            isinstance(prio, bool) or not isinstance(prio, int) or not 1 <= prio <= 10
        ):
            raise ValueError(f"Backup schedule '{name}' has an invalid priority")


def _coercer_for(col_type: Any) -> Any:
    """Return a function that turns an exported JSON value back into a bind value."""
    type_name = type(col_type).__name__.upper()

    def _uuid(v: Any) -> Any:
        return uuid.UUID(v) if isinstance(v, str) else v

    def _dt(v: Any) -> Any:
        return datetime.fromisoformat(v) if isinstance(v, str) else v

    def _vector(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("["):
            return [float(x) for x in json.loads(v)]
        return v

    if type_name in ("UUID", "UUIDTYPE"):
        return _uuid
    if type_name in ("DATETIME", "TIMESTAMP"):
        return _dt
    if type_name == "VECTOR":
        return _vector
    return lambda v: v


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _json_serializer(obj: Any) -> Any:
    """Default serializer for ``json.dumps`` — handles datetimes and UUIDs."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _json_safe(value: Any) -> Any:
    """Make a database value safe for JSON serialization."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = ["BackupInfo", "BackupService"]
