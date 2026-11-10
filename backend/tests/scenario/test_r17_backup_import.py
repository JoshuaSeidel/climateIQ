"""Backup import: reject schedules that would drive the thermostat wrongly, keep the
live system_config, sync in-memory settings, and leave no file behind on rejection."""

import json
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.api.middleware import RateLimitMiddleware
from backend.config import SETTINGS
from backend.models.database import close_db, init_db
from backend.tests.scenario.harness import House


def _reset_rate_limit() -> None:
    node = getattr(app, "middleware_stack", None)
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            node._requests.clear()
            return
        node = getattr(node, "app", None)


async def _import(c: AsyncClient, payload: Any) -> Any:
    return await c.post(
        "/api/v1/backup/import",
        files={"file": ("b.json", json.dumps(payload).encode(), "application/json")},
    )


async def test_backup_import_guards(
    house: House, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import backend.api.main as m
    import backend.api.routes.backup as bk

    monkeypatch.setattr(m.app_state, "zone_manager", None)
    monkeypatch.setattr(bk._backup_service, "_backup_dir", tmp_path)
    monkeypatch.setattr(SETTINGS, "temperature_unit", "F")
    await close_db()
    await init_db()
    _reset_rate_limit()
    house.at(2026, 1, 12, 10, 0)
    await house.add_zone("Bedroom", 64)
    await house.add_schedule("Day", "08:00", "12:00", 70.5)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/v1/backup/export")).status_code in (200, 201)
        exported = next(tmp_path.glob("*.json"))
        good = json.loads(exported.read_text())
        exported.unlink()

        def with_schedule(**changes: Any) -> dict[str, Any]:
            payload = json.loads(json.dumps(good))
            row = dict(payload["tables"]["schedules"][0])
            row.update(changes)
            row["id"] = str(uuid.uuid4())
            payload["tables"]["schedules"].append(row)
            payload["backup_id"] = str(uuid.uuid4())
            return payload

        # A schedule far outside the safety range is rejected, nothing is kept on disk.
        before = len(list(tmp_path.glob("*.json")))
        for bad in (
            {"name": "Hot", "target_temp_c": 500.0},
            {"name": "Prio", "priority": 99},
            {"name": "Time", "start_time": "99:99"},
        ):
            r = await _import(c, with_schedule(**bad))
            assert r.status_code == 400, (bad, r.status_code, r.text)
        assert len(list(tmp_path.glob("*.json"))) == before, (
            "rejected uploads must not stay on disk"
        )

        # An upload that reuses a REAL backup's id must never overwrite or delete it.
        real_id = good["backup_id"]
        (tmp_path / "climateiq_backup_real.json").write_text(json.dumps(good))
        payload = with_schedule(name="Hot", target_temp_c=500.0)
        payload["backup_id"] = real_id
        assert (await _import(c, payload)).status_code == 400
        assert (tmp_path / "climateiq_backup_real.json").exists()
        (tmp_path / "climateiq_backup_real.json").unlink()

        # A non-object body is a 400, not a 500.
        assert (await _import(c, [1, 2, 3])).status_code == 400

        # An empty system_config must not wipe the live singleton.
        payload = json.loads(json.dumps(good))
        payload["tables"]["system_config"] = []
        payload["backup_id"] = str(uuid.uuid4())
        r = await _import(c, payload)
        assert r.status_code == 200, r.text
        assert (await c.get("/api/v1/settings")).status_code == 200

        # A good backup still restores.
        assert (await _import(c, good)).status_code == 200
