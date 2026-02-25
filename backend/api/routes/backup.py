"""Backup and restore API routes for ClimateIQ."""

from __future__ import annotations

import json
import logging
import uuid as _uuid_mod
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.services.backup_service import BackupService

logger = logging.getLogger(__name__)

router = APIRouter()

_backup_service = BackupService()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class BackupInfoResponse(BaseModel):
    """Metadata about a stored backup."""

    backup_id: str
    filename: str
    created_at: str
    size_bytes: int
    table_counts: dict[str, int] = Field(default_factory=dict)


class BackupExportResponse(BaseModel):
    """Response after creating a backup."""

    backup_id: str
    message: str


class BackupRestoreResponse(BaseModel):
    """Response after restoring a backup."""

    message: str
    backup_id: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/export", response_model=BackupExportResponse)
async def export_backup(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BackupExportResponse:
    """Create a full database backup and return its ID."""
    try:
        backup_id = await _backup_service.create_backup(db)
        return BackupExportResponse(
            backup_id=backup_id,
            message="Backup created successfully",
        )
    except Exception as exc:
        logger.exception("Backup export failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup failed: {exc}",
        ) from exc


@router.post("/import", response_model=BackupRestoreResponse)
async def import_backup(
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BackupRestoreResponse:
    """Restore the database from an uploaded JSON backup file.

    WARNING: This is a destructive operation that replaces existing data.
    """
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be a .json backup file",
        )

    _MAX_BACKUP_SIZE = 50 * 1024 * 1024  # 50 MB

    try:
        raw = await file.read(_MAX_BACKUP_SIZE + 1)
        if len(raw) > _MAX_BACKUP_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Backup file exceeds the 50 MB limit",
            )
        backup_payload: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON file: {exc}",
        ) from exc

    backup_id = backup_payload.get("backup_id", "uploaded")

    # Write the uploaded backup to disk so BackupService can find it
    import uuid
    from datetime import UTC, datetime

    backup_dir = _backup_service.get_backup_dir()
    timestamp_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not backup_payload.get("backup_id"):
        backup_payload["backup_id"] = str(uuid.uuid4())
    backup_id = str(backup_payload["backup_id"])
    filename = f"climateiq_backup_{timestamp_str}_{backup_id[:8]}.json"
    filepath = backup_dir / filename
    filepath.write_text(
        json.dumps(backup_payload, default=str, indent=2),
        encoding="utf-8",
    )

    try:
        await _backup_service.restore_backup(db, backup_id)
        return BackupRestoreResponse(
            message="Backup restored successfully",
            backup_id=backup_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Backup restore failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Restore failed: {exc}",
        ) from exc


@router.get("", response_model=list[BackupInfoResponse])
async def list_backups() -> list[BackupInfoResponse]:
    """List all available backups."""
    backups = await _backup_service.list_backups()
    return [
        BackupInfoResponse(
            backup_id=b.backup_id,
            filename=b.filename,
            created_at=b.created_at,
            size_bytes=b.size_bytes,
            table_counts=b.table_counts,
        )
        for b in backups
    ]


@router.delete("/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(backup_id: _uuid_mod.UUID) -> None:
    """Delete a backup by ID."""
    try:
        await _backup_service.delete_backup(str(backup_id))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


__all__ = ["router"]
