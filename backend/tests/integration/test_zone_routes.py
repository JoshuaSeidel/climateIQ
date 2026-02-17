"""Integration tests for zone API routes (/api/v1/zones).

Uses mocked database dependencies so tests run without a live database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from backend.api.dependencies import get_db
from backend.api.main import app
from backend.models.database import Zone
from backend.models.enums import ZoneType
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------

ZONE_ID_1 = uuid4()
ZONE_ID_2 = uuid4()
NOW = datetime.now(UTC)


def _make_zone(
    *,
    zone_id: None = None,
    name: str = "Living Room",
    zone_type: ZoneType = ZoneType.living_area,
    floor: int = 1,
    is_active: bool = True,
) -> MagicMock:
    """Return a MagicMock that behaves like a Zone ORM instance."""
    zone = MagicMock(spec=Zone)
    zone.id = zone_id or uuid4()
    zone.name = name
    zone.type = zone_type
    zone.floor = floor
    zone.is_active = is_active
    zone.description = None
    zone.comfort_preferences = {}
    zone.thermal_profile = {}
    zone.created_at = NOW
    zone.updated_at = NOW
    zone.sensors = []
    zone.devices = []
    return zone


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_db() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture(autouse=True)
def _override_db(mock_db: AsyncMock) -> Generator[None]:
    """Override the get_db dependency for every test, then clean up."""
    app.dependency_overrides[get_db] = lambda: mock_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
async def client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helper to build mock execute results
# ---------------------------------------------------------------------------


def _scalars_unique_all(items: list[MagicMock]) -> AsyncMock:
    """Mock for ``result.scalars().unique().all()``."""
    result = MagicMock()
    scalars = MagicMock()
    unique = MagicMock()
    unique.all.return_value = items
    scalars.unique.return_value = unique
    result.scalars.return_value = scalars
    return result


def _scalar_one_or_none(item: MagicMock | None) -> MagicMock:
    """Mock for ``result.scalar_one_or_none()``."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = item
    return result


# ===================================================================
# GET /api/v1/zones
# ===================================================================


class TestListZones:
    async def test_list_zones_empty(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalars_unique_all([])

        resp = await client.get("/api/v1/zones")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_zones_with_data(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone1 = _make_zone(name="Living Room", floor=1)
        zone2 = _make_zone(name="Bedroom", zone_type=ZoneType.bedroom, floor=2)
        mock_db.execute.return_value = _scalars_unique_all([zone1, zone2])

        resp = await client.get("/api/v1/zones")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Living Room"
        assert data[1]["name"] == "Bedroom"

    async def test_list_zones_filter_is_active(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        zone = _make_zone(name="Active Zone", is_active=True)
        mock_db.execute.return_value = _scalars_unique_all([zone])

        resp = await client.get("/api/v1/zones", params={"is_active": "true"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["is_active"] is True

    async def test_list_zones_filter_by_floor(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        zone = _make_zone(name="Basement", zone_type=ZoneType.basement, floor=0)
        mock_db.execute.return_value = _scalars_unique_all([zone])

        resp = await client.get("/api/v1/zones", params={"floor": 0})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Basement"


# ===================================================================
# GET /api/v1/zones/{zone_id}
# ===================================================================


class TestGetZone:
    async def test_get_zone_exists(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone = _make_zone(name="Office")
        zone.id = ZONE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(zone)

        resp = await client.get(f"/api/v1/zones/{ZONE_ID_1}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Office"
        assert data["id"] == str(ZONE_ID_1)

    async def test_get_zone_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.get(f"/api/v1/zones/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]


# ===================================================================
# POST /api/v1/zones
# ===================================================================


class TestCreateZone:
    async def test_create_zone_valid(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        created_zone = _make_zone(name="Kitchen", zone_type=ZoneType.kitchen, floor=1)

        async def _fake_refresh(obj: object, attribute_names: list[str] | None = None) -> None:
            # Simulate refresh populating the object — no-op since mock already has attrs
            pass

        mock_db.refresh = AsyncMock(side_effect=_fake_refresh)
        mock_db.commit = AsyncMock()

        # After add + commit + refresh the route calls model_validate on the zone.
        # We need to intercept the Zone(...) constructor to return our mock.
        with patch("backend.api.routes.zones.Zone", return_value=created_zone):
            resp = await client.post(
                "/api/v1/zones",
                json={
                    "name": "Kitchen",
                    "type": "kitchen",
                    "floor": 1,
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Kitchen"
        assert data["type"] == "kitchen"
        assert data["floor"] == 1
        mock_db.add.assert_called_once_with(created_zone)
        mock_db.commit.assert_awaited_once()

    async def test_create_zone_missing_name_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/zones",
            json={"type": "bedroom"},
        )
        # FastAPI returns 422 for missing required fields
        assert resp.status_code == 422

    async def test_create_zone_missing_type_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/zones",
            json={"name": "Test Zone"},
        )
        assert resp.status_code == 422

    async def test_create_zone_invalid_type_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/zones",
            json={"name": "Test Zone", "type": "nonexistent_type"},
        )
        assert resp.status_code == 422


# ===================================================================
# PUT /api/v1/zones/{zone_id}
# ===================================================================


class TestUpdateZone:
    async def test_update_zone_valid(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone = _make_zone(name="Old Name")
        zone.id = ZONE_ID_1
        # First execute call: _fetch_zone
        mock_db.execute.return_value = _scalar_one_or_none(zone)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = await client.put(
            f"/api/v1/zones/{ZONE_ID_1}",
            json={"name": "New Name"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(ZONE_ID_1)
        # setattr should have been called on the mock
        mock_db.commit.assert_awaited_once()

    async def test_update_zone_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.put(
            f"/api/v1/zones/{missing_id}",
            json={"name": "Anything"},
        )

        assert resp.status_code == 404

    async def test_update_zone_empty_body_returns_400(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        zone = _make_zone(name="Existing")
        zone.id = ZONE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(zone)

        resp = await client.put(f"/api/v1/zones/{ZONE_ID_1}", json={})

        assert resp.status_code == 400
        assert "No fields provided" in resp.json()["detail"]

    async def test_update_zone_partial_fields(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        zone = _make_zone(name="Room", floor=1, is_active=True)
        zone.id = ZONE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(zone)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = await client.put(
            f"/api/v1/zones/{ZONE_ID_1}",
            json={"floor": 3, "is_active": False},
        )

        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()


# ===================================================================
# DELETE /api/v1/zones/{zone_id}
# ===================================================================


class TestDeleteZone:
    async def test_delete_zone_exists(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone = _make_zone(name="To Delete")
        zone.id = ZONE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(zone)
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        resp = await client.delete(f"/api/v1/zones/{ZONE_ID_1}")

        assert resp.status_code == 204
        mock_db.delete.assert_awaited_once_with(zone)
        mock_db.commit.assert_awaited_once()

    async def test_delete_zone_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.delete(f"/api/v1/zones/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]


# ===================================================================
# GET /api/v1/zones/{zone_id}/readings
# ===================================================================


class TestZoneReadings:
    async def test_readings_zone_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.get(f"/api/v1/zones/{missing_id}/readings")

        assert resp.status_code == 404

    async def test_readings_empty_sensors(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone = _make_zone(name="Empty Zone")
        zone.id = ZONE_ID_1

        # First call: _fetch_zone; second call: sensor IDs query
        zone_result = _scalar_one_or_none(zone)
        sensor_result = MagicMock()
        sensor_result.all.return_value = []

        mock_db.execute = AsyncMock(side_effect=[zone_result, sensor_result])

        resp = await client.get(f"/api/v1/zones/{ZONE_ID_1}/readings")

        assert resp.status_code == 200
        assert resp.json() == []
