"""Tests for backend.services.backup_service — backup, restore, list, delete."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.backup_service import (
    BackupInfo,
    BackupService,
    _json_safe,
    _json_serializer,
    _validate_table_name,
)

# ===================================================================
# _validate_table_name
# ===================================================================


class TestValidateTableName:
    """Tests for the SQL-injection-safe table name validator."""

    def test_valid_table_name_passes(self) -> None:
        result = _validate_table_name("zones")
        assert result == "zones"

    def test_valid_table_with_underscore(self) -> None:
        result = _validate_table_name("sensor_readings")
        assert result == "sensor_readings"

    def test_invalid_table_name_raises(self) -> None:
        with pytest.raises(ValueError, match="not in the backup allowlist"):
            _validate_table_name("nonexistent_table")

    def test_special_characters_raise(self) -> None:
        with pytest.raises(ValueError, match="not in the backup allowlist"):
            _validate_table_name("zones; DROP TABLE zones")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="not in the backup allowlist"):
            _validate_table_name("")


# ===================================================================
# _json_serializer
# ===================================================================


class TestJsonSerializer:
    """Tests for the json.dumps default serializer."""

    def test_datetime_serialized_to_iso(self) -> None:
        dt = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
        result = _json_serializer(dt)
        assert result == "2026-01-15T12:30:00+00:00"

    def test_uuid_serialized_to_string(self) -> None:
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = _json_serializer(uid)
        assert result == "12345678-1234-5678-1234-567812345678"

    def test_str_fallback(self) -> None:
        """Objects with __str__ are converted via str()."""

        class Custom:
            def __str__(self) -> str:
                return "custom-value"

        result = _json_serializer(Custom())
        assert result == "custom-value"

    def test_non_serializable_raises(self) -> None:
        """Objects without a callable __str__ raise TypeError."""

        class NoStr:
            __str__ = None  # type: ignore[assignment]

        # hasattr still returns True (attr exists but is None), so str()
        # raises TypeError when it tries to call None.
        with pytest.raises(TypeError):
            _json_serializer(NoStr())


# ===================================================================
# _json_safe
# ===================================================================


class TestJsonSafe:
    """Tests for the database-value sanitizer."""

    def test_datetime_to_iso(self) -> None:
        dt = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)
        assert _json_safe(dt) == "2026-06-01T08:00:00+00:00"

    def test_uuid_to_string(self) -> None:
        uid = uuid.uuid4()
        assert _json_safe(uid) == str(uid)

    def test_bytes_decoded(self) -> None:
        assert _json_safe(b"hello") == "hello"

    def test_bytes_with_bad_encoding(self) -> None:
        result = _json_safe(b"\xff\xfe")
        assert isinstance(result, str)

    def test_passthrough_for_other_types(self) -> None:
        assert _json_safe(42) == 42
        assert _json_safe("text") == "text"
        assert _json_safe(None) is None
        assert _json_safe(3.14) == 3.14


# ===================================================================
# BackupService
# ===================================================================


def _make_mock_session(
    columns: list[str] | None = None,
    rows: list[tuple[object, ...]] | None = None,
) -> AsyncMock:
    """Create a mock AsyncSession that returns configurable query results."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.keys.return_value = columns or ["id", "name"]
    mock_result.fetchall.return_value = rows or []
    mock_session.execute.return_value = mock_result
    return mock_session


class TestBackupService:
    """Tests for the BackupService CRUD operations."""

    async def test_create_backup_returns_uuid(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        mock_session = _make_mock_session()

        backup_id = await service.create_backup(mock_session)

        # Should be a valid UUID
        uuid.UUID(backup_id)
        assert len(backup_id) == 36

    async def test_create_backup_creates_json_file(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        mock_session = _make_mock_session()

        backup_id = await service.create_backup(mock_session)

        # Find the created file
        files = list(tmp_path.glob("climateiq_backup_*.json"))  # noqa: ASYNC240
        assert len(files) == 1

        # Verify contents
        data = json.loads(files[0].read_text())
        assert data["backup_id"] == backup_id
        assert data["version"] == "1.0"
        assert "tables" in data
        assert "table_counts" in data
        assert "created_at" in data

    async def test_create_backup_with_data(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        mock_session = _make_mock_session(
            columns=["id", "name"],
            rows=[(1, "Zone A"), (2, "Zone B")],
        )

        backup_id = await service.create_backup(mock_session)

        files = list(tmp_path.glob("climateiq_backup_*.json"))  # noqa: ASYNC240
        data = json.loads(files[0].read_text())
        # The first table (zones) should have rows
        assert data["table_counts"]["zones"] == 2
        assert data["backup_id"] == backup_id

    async def test_list_backups_returns_backup_info(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        mock_session = _make_mock_session()

        # Create two backups
        id1 = await service.create_backup(mock_session)
        id2 = await service.create_backup(mock_session)

        backups = await service.list_backups()

        assert len(backups) == 2
        assert all(isinstance(b, BackupInfo) for b in backups)
        backup_ids = {b.backup_id for b in backups}
        assert id1 in backup_ids
        assert id2 in backup_ids

        # Each backup should have valid metadata
        for b in backups:
            assert b.filename.startswith("climateiq_backup_")
            assert b.size_bytes > 0
            assert isinstance(b.table_counts, dict)

    async def test_list_backups_empty_dir(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        backups = await service.list_backups()
        assert backups == []

    async def test_delete_backup_removes_file(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        mock_session = _make_mock_session()

        backup_id = await service.create_backup(mock_session)
        files_before = list(tmp_path.glob("climateiq_backup_*.json"))  # noqa: ASYNC240
        assert len(files_before) == 1

        await service.delete_backup(backup_id)

        files_after = list(tmp_path.glob("climateiq_backup_*.json"))  # noqa: ASYNC240
        assert len(files_after) == 0

    async def test_delete_backup_not_found_raises(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        fake_id = str(uuid.uuid4())

        with pytest.raises(FileNotFoundError, match="No backup found"):
            await service.delete_backup(fake_id)

    async def test_restore_backup_not_found_raises(self, tmp_path: Path) -> None:
        service = BackupService(backup_dir=tmp_path)
        mock_session = _make_mock_session()
        fake_id = str(uuid.uuid4())

        with pytest.raises(FileNotFoundError, match="No backup found"):
            await service.restore_backup(mock_session, fake_id)

    async def test_restore_backup_calls_db(self, tmp_path: Path) -> None:
        """Create a backup, then restore it — verify DB calls are made."""
        service = BackupService(backup_dir=tmp_path)
        create_session = _make_mock_session(
            columns=["id", "name"],
            rows=[(1, "Zone A")],
        )
        backup_id = await service.create_backup(create_session)

        restore_session = AsyncMock()
        restore_result = MagicMock()
        restore_result.keys.return_value = ["id", "name"]
        restore_result.fetchall.return_value = []
        restore_session.execute.return_value = restore_result

        await service.restore_backup(restore_session, backup_id)

        # Should have called execute for DELETE and INSERT operations
        assert restore_session.execute.call_count > 0
        # Should have committed
        restore_session.commit.assert_awaited_once()

    async def test_backup_dir_created_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "backups"
        service = BackupService(backup_dir=nested)
        mock_session = _make_mock_session()

        await service.create_backup(mock_session)

        assert nested.exists()
        assert nested.is_dir()
