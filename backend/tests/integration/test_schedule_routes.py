"""Integration tests for schedule API routes with mocked database."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from backend.api.dependencies import get_db
from backend.api.main import app
from backend.models.database import Schedule
from httpx import ASGITransport, AsyncClient

BASE = "/api/v1/schedules"


# ============================================================================
# Fixtures
# ============================================================================


def _make_schedule(**overrides: object) -> MagicMock:
    """Build a MagicMock that looks like a Schedule ORM instance."""
    sched = MagicMock(spec=Schedule)
    sched.id = overrides.get("id", uuid4())
    sched.name = overrides.get("name", "Morning Heat")
    sched.zone_id = overrides.get("zone_id", None)
    sched.days_of_week = overrides.get("days_of_week", [0, 1, 2, 3, 4])
    sched.start_time = overrides.get("start_time", "06:00")
    sched.end_time = overrides.get("end_time", "09:00")
    sched.target_temp_c = overrides.get("target_temp_c", 22.0)
    sched.hvac_mode = overrides.get("hvac_mode", "heat")
    sched.is_enabled = overrides.get("is_enabled", True)
    sched.priority = overrides.get("priority", 5)
    sched.created_at = overrides.get("created_at", datetime.now(UTC))
    sched.updated_at = overrides.get("updated_at", datetime.now(UTC))
    return sched


def _scalars_result(items: list[MagicMock]) -> MagicMock:
    """Create a mock result whose .scalars().all() returns *items*."""
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = items
    result = MagicMock()
    result.scalars.return_value = scalars_mock
    result.scalar_one_or_none.return_value = items[0] if items else None
    return result


@pytest.fixture
def mock_db() -> Generator[AsyncMock]:
    session = AsyncMock()
    app.dependency_overrides[get_db] = lambda: session
    yield session
    app.dependency_overrides.clear()


@pytest.fixture
async def api_client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ============================================================================
# GET /api/v1/schedules — list schedules
# ============================================================================


class TestListSchedules:
    async def test_empty_list(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalars_result([])

        resp = await api_client.get(BASE)

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_schedules(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        sched = _make_schedule()
        mock_db.execute.return_value = _scalars_result([sched])

        resp = await api_client.get(BASE)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Morning Heat"
        assert data[0]["target_temp_c"] == 22.0
        assert data[0]["is_enabled"] is True


# ============================================================================
# GET /api/v1/schedules/upcoming
# ============================================================================


class TestUpcomingSchedules:
    async def test_upcoming_empty(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalars_result([])

        resp = await api_client.get(f"{BASE}/upcoming")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_upcoming_with_schedule(
        self, api_client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        sched = _make_schedule(
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time="23:00",
            end_time="23:30",
        )
        mock_db.execute.return_value = _scalars_result([sched])

        resp = await api_client.get(f"{BASE}/upcoming", params={"hours": 168})

        assert resp.status_code == 200
        data = resp.json()
        # Should find at least one upcoming occurrence within 168 hours
        assert len(data) >= 1
        assert data[0]["schedule_name"] == "Morning Heat"


# ============================================================================
# GET /api/v1/schedules/conflicts
# ============================================================================


class TestConflicts:
    async def test_no_conflicts(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        s1 = _make_schedule(name="Morning", start_time="06:00", end_time="09:00")
        s2 = _make_schedule(name="Evening", start_time="17:00", end_time="22:00")
        mock_db.execute.return_value = _scalars_result([s1, s2])

        resp = await api_client.get(f"{BASE}/conflicts")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_conflicts(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        s1 = _make_schedule(
            name="Morning",
            start_time="06:00",
            end_time="12:00",
            priority=5,
        )
        s2 = _make_schedule(
            name="Midday",
            start_time="10:00",
            end_time="14:00",
            priority=5,
        )
        mock_db.execute.return_value = _scalars_result([s1, s2])

        resp = await api_client.get(f"{BASE}/conflicts")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["conflict_type"] == "priority_tie"
        assert data[0]["schedule_name"] == "Morning"
        assert data[0]["conflicting_schedule_name"] == "Midday"

    async def test_overlap_different_priority(
        self, api_client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        s1 = _make_schedule(
            name="High",
            start_time="06:00",
            end_time="12:00",
            priority=8,
        )
        s2 = _make_schedule(
            name="Low",
            start_time="10:00",
            end_time="14:00",
            priority=3,
        )
        mock_db.execute.return_value = _scalars_result([s1, s2])

        resp = await api_client.get(f"{BASE}/conflicts")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["conflict_type"] == "overlap"


# ============================================================================
# GET /api/v1/schedules/{id}
# ============================================================================


class TestGetSchedule:
    async def test_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        sched = _make_schedule()
        result_mock = _scalars_result([sched])
        mock_db.execute.return_value = result_mock

        resp = await api_client.get(f"{BASE}/{sched.id}")

        assert resp.status_code == 200
        assert resp.json()["name"] == "Morning Heat"

    async def test_not_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        missing_id = uuid4()
        resp = await api_client.get(f"{BASE}/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]


# ============================================================================
# POST /api/v1/schedules — create
# ============================================================================


class TestCreateSchedule:
    VALID_PAYLOAD: ClassVar[dict[str, object]] = {
        "name": "Night Cool",
        "start_time": "22:00",
        "end_time": "06:00",
        "target_temp_c": 18.5,
        "days_of_week": [0, 1, 2, 3, 4],
        "hvac_mode": "cooling",
        "priority": 3,
    }

    async def test_create_valid(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        created = _make_schedule(
            name="Night Cool",
            start_time="22:00",
            end_time="06:00",
            target_temp_c=18.5,
            hvac_mode="cooling",
            priority=3,
        )

        # The route calls db.execute (no zone lookup since zone_id is None),
        # then db.add, db.commit, db.refresh.
        # After refresh, the schedule object is used to build the response.
        async def fake_refresh(obj: object) -> None:
            # Simulate SQLAlchemy refresh by copying attrs from our mock
            for attr in (
                "id",
                "name",
                "zone_id",
                "days_of_week",
                "start_time",
                "end_time",
                "target_temp_c",
                "hvac_mode",
                "is_enabled",
                "priority",
                "created_at",
                "updated_at",
            ):
                setattr(obj, attr, getattr(created, attr))

        mock_db.refresh.side_effect = fake_refresh
        mock_db.commit.return_value = None

        resp = await api_client.post(BASE, json=self.VALID_PAYLOAD)

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Night Cool"
        assert data["target_temp_c"] == 18.5
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    async def test_temperature_below_safety_returns_422(
        self, api_client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        payload = {**self.VALID_PAYLOAD, "target_temp_c": 3.0}

        resp = await api_client.post(BASE, json=payload)

        assert resp.status_code == 422

    async def test_temperature_above_safety_returns_422(
        self, api_client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        payload = {**self.VALID_PAYLOAD, "target_temp_c": 40.0}

        resp = await api_client.post(BASE, json=payload)

        assert resp.status_code == 422

    async def test_zone_not_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        zone_id = uuid4()
        payload = {**self.VALID_PAYLOAD, "zone_id": str(zone_id)}

        # Zone lookup returns None
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        resp = await api_client.post(BASE, json=payload)

        assert resp.status_code == 404
        assert "Zone" in resp.json()["detail"]


# ============================================================================
# PUT /api/v1/schedules/{id} — update
# ============================================================================


class TestUpdateSchedule:
    async def test_update_valid(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        sched = _make_schedule()
        result_mock = _scalars_result([sched])
        mock_db.execute.return_value = result_mock

        async def fake_refresh(obj: object) -> None:
            pass

        mock_db.refresh.side_effect = fake_refresh

        resp = await api_client.put(
            f"{BASE}/{sched.id}",
            json={"name": "Updated Name"},
        )

        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()

    async def test_update_not_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        missing_id = uuid4()
        resp = await api_client.put(
            f"{BASE}/{missing_id}",
            json={"name": "Nope"},
        )

        assert resp.status_code == 404


# ============================================================================
# DELETE /api/v1/schedules/{id}
# ============================================================================


class TestDeleteSchedule:
    async def test_delete_exists(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        sched = _make_schedule()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = sched
        mock_db.execute.return_value = result_mock

        resp = await api_client.delete(f"{BASE}/{sched.id}")

        assert resp.status_code == 204
        mock_db.delete.assert_awaited_once_with(sched)
        mock_db.commit.assert_awaited_once()

    async def test_delete_not_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        missing_id = uuid4()
        resp = await api_client.delete(f"{BASE}/{missing_id}")

        assert resp.status_code == 404


# ============================================================================
# POST /api/v1/schedules/{id}/enable
# ============================================================================


class TestEnableSchedule:
    async def test_enable_exists(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        sched = _make_schedule(is_enabled=False)
        result_mock = _scalars_result([sched])
        mock_db.execute.return_value = result_mock

        async def fake_refresh(obj: object) -> None:
            pass

        mock_db.refresh.side_effect = fake_refresh

        resp = await api_client.post(f"{BASE}/{sched.id}/enable")

        assert resp.status_code == 200
        assert sched.is_enabled is True
        mock_db.commit.assert_awaited_once()

    async def test_enable_not_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        missing_id = uuid4()
        resp = await api_client.post(f"{BASE}/{missing_id}/enable")

        assert resp.status_code == 404


# ============================================================================
# POST /api/v1/schedules/{id}/disable
# ============================================================================


class TestDisableSchedule:
    async def test_disable_exists(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        sched = _make_schedule(is_enabled=True)
        result_mock = _scalars_result([sched])
        mock_db.execute.return_value = result_mock

        async def fake_refresh(obj: object) -> None:
            pass

        mock_db.refresh.side_effect = fake_refresh

        resp = await api_client.post(f"{BASE}/{sched.id}/disable")

        assert resp.status_code == 200
        assert sched.is_enabled is False
        mock_db.commit.assert_awaited_once()

    async def test_disable_not_found(self, api_client: AsyncClient, mock_db: AsyncMock) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        missing_id = uuid4()
        resp = await api_client.post(f"{BASE}/{missing_id}/disable")

        assert resp.status_code == 404
