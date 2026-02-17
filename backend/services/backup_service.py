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

        tables: dict[str, list[dict[str, Any]]] = backup_payload.get("tables", {})
        if not tables:
            raise ValueError("Backup contains no table data")

        # Restore in reverse dependency order (children first for deletes,
        # then parents first for inserts).
        delete_order = list(reversed(_BACKUP_TABLES))

        logger.info("Restoring backup %s — clearing existing data", backup_id)

        # Phase 1: delete existing rows (children → parents)
        for table_name in delete_order:
            if table_name in tables:
                try:
                    safe_name = _validate_table_name(table_name)
                    await db_session.execute(text(f'DELETE FROM "{safe_name}"'))  # noqa: S608
                    logger.debug("Cleared table %s", table_name)
                except Exception:
                    logger.warning("Could not clear table %s (may not exist)", table_name)

        # Phase 2: insert backup rows (parents → children)
        total_restored = 0
        for table_name in _BACKUP_TABLES:
            rows = tables.get(table_name, [])
            if not rows:
                continue
            try:
                count = await self._restore_table(db_session, table_name, rows)
                total_restored += count
                logger.debug("Restored %d rows into %s", count, table_name)
            except Exception:
                logger.exception("Failed to restore table %s", table_name)
                raise

        await db_session.commit()
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
        """Insert *rows* into *table_name* using raw SQL.

        Returns the number of rows inserted.
        """
        if not rows:
            return 0

        # Use the columns from the first row as the schema
        columns = list(rows[0].keys())
        col_list = ", ".join(f'"{c}"' for c in columns)
        param_list = ", ".join(f":{c}" for c in columns)
        safe_name = _validate_table_name(table_name)
        insert_sql = text(
            f'INSERT INTO "{safe_name}" ({col_list}) VALUES ({param_list})'  # noqa: S608
        )

        count = 0
        for row in rows:
            try:
                await db_session.execute(insert_sql, row)
                count += 1
            except Exception:
                logger.warning(
                    "Failed to insert row into %s: %s",
                    table_name,
                    {k: str(v)[:50] for k, v in row.items()},
                )
                # Continue with remaining rows
                continue

        return count


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
