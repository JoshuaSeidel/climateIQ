"""Integration tests for sensor API routes (/api/v1/sensors).

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
from backend.models.database import Sensor, Zone
from backend.models.enums import SensorType
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------

ZONE_ID = uuid4()
SENSOR_ID_1 = uuid4()
SENSOR_ID_2 = uuid4()
NOW = datetime.now(UTC)


def _make_sensor(
    *,
    sensor_id: None = None,
    name: str = "Living Room Multisensor",
    sensor_type: SensorType = SensorType.multisensor,
    zone_id: None = None,
    is_active: bool = True,
) -> MagicMock:
    """Return a MagicMock that behaves like a Sensor ORM instance."""
    sensor = MagicMock(spec=Sensor)
    sensor.id = sensor_id or uuid4()
    sensor.zone_id = zone_id or ZONE_ID
    sensor.name = name
    sensor.type = sensor_type
    sensor.manufacturer = None
    sensor.model = None
    sensor.firmware_version = None
    sensor.ha_entity_id = None
    sensor.entity_id = None
    sensor.capabilities = {}
    sensor.calibration_offsets = {}
    sensor.is_active = is_active
    sensor.last_seen = None
    sensor.config = {}
    sensor.created_at = NOW
    return sensor


def _make_zone_mock() -> MagicMock:
    """Return a minimal Zone mock for zone-existence checks."""
    zone = MagicMock(spec=Zone)
    zone.id = ZONE_ID
    zone.name = "Test Zone"
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


def _scalars_all(items: list[MagicMock]) -> MagicMock:
    """Mock for ``result.scalars().all()``."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = items
    result.scalars.return_value = scalars
    return result


def _scalar_one_or_none(item: MagicMock | None) -> MagicMock:
    """Mock for ``result.scalar_one_or_none()``."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = item
    return result


# ===================================================================
# GET /api/v1/sensors
# ===================================================================


class TestListSensors:
    async def test_list_sensors_empty(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalars_all([])

        resp = await client.get("/api/v1/sensors")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_sensors_with_data(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        s1 = _make_sensor(name="Sensor A", sensor_type=SensorType.multisensor)
        s2 = _make_sensor(name="Sensor B", sensor_type=SensorType.temp_only)
        mock_db.execute.return_value = _scalars_all([s1, s2])

        resp = await client.get("/api/v1/sensors")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Sensor A"
        assert data[1]["name"] == "Sensor B"

    async def test_list_sensors_filter_by_zone_id(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        s1 = _make_sensor(name="Zone Sensor")
        mock_db.execute.return_value = _scalars_all([s1])

        resp = await client.get("/api/v1/sensors", params={"zone_id": str(ZONE_ID)})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["zone_id"] == str(ZONE_ID)

    async def test_list_sensors_filter_by_type(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        s1 = _make_sensor(name="Temp Sensor", sensor_type=SensorType.temp_only)
        mock_db.execute.return_value = _scalars_all([s1])

        resp = await client.get("/api/v1/sensors", params={"type": "temp_only"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "temp_only"


# ===================================================================
# GET /api/v1/sensors/{sensor_id}
# ===================================================================


class TestGetSensor:
    async def test_get_sensor_exists(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        sensor = _make_sensor(name="Office Sensor")
        sensor.id = SENSOR_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(sensor)

        resp = await client.get(f"/api/v1/sensors/{SENSOR_ID_1}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Office Sensor"
        assert data["id"] == str(SENSOR_ID_1)

    async def test_get_sensor_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.get(f"/api/v1/sensors/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]


# ===================================================================
# POST /api/v1/sensors
# ===================================================================


class TestCreateSensor:
    async def test_create_sensor_valid(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone_mock = _make_zone_mock()
        created_sensor = _make_sensor(name="New Sensor", sensor_type=SensorType.temp_humidity)

        # First execute: zone existence check; no second execute needed
        mock_db.execute = AsyncMock(return_value=_scalar_one_or_none(zone_mock))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch("backend.api.routes.sensors.Sensor", return_value=created_sensor):
            resp = await client.post(
                "/api/v1/sensors",
                json={
                    "name": "New Sensor",
                    "type": "temp_humidity",
                    "zone_id": str(ZONE_ID),
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Sensor"
        assert data["type"] == "temp_humidity"
        mock_db.add.assert_called_once_with(created_sensor)
        mock_db.commit.assert_awaited_once()

    async def test_create_sensor_zone_not_found(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_zone = uuid4()

        resp = await client.post(
            "/api/v1/sensors",
            json={
                "name": "Orphan Sensor",
                "type": "multisensor",
                "zone_id": str(missing_zone),
            },
        )

        assert resp.status_code == 404
        assert str(missing_zone) in resp.json()["detail"]

    async def test_create_sensor_missing_name_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/sensors",
            json={"type": "multisensor", "zone_id": str(ZONE_ID)},
        )
        assert resp.status_code == 422

    async def test_create_sensor_missing_type_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/sensors",
            json={"name": "Test", "zone_id": str(ZONE_ID)},
        )
        assert resp.status_code == 422

    async def test_create_sensor_missing_zone_id_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/sensors",
            json={"name": "Test", "type": "multisensor"},
        )
        assert resp.status_code == 422


# ===================================================================
# PUT /api/v1/sensors/{sensor_id}
# ===================================================================


class TestUpdateSensor:
    async def test_update_sensor_valid(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        sensor = _make_sensor(name="Old Name")
        sensor.id = SENSOR_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(sensor)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = await client.put(
            f"/api/v1/sensors/{SENSOR_ID_1}",
            json={"name": "Updated Name"},
        )

        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()

    async def test_update_sensor_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.put(
            f"/api/v1/sensors/{missing_id}",
            json={"name": "Anything"},
        )

        assert resp.status_code == 404

    async def test_update_sensor_empty_body_returns_400(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        sensor = _make_sensor(name="Existing")
        sensor.id = SENSOR_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(sensor)

        resp = await client.put(f"/api/v1/sensors/{SENSOR_ID_1}", json={})

        assert resp.status_code == 400
        assert "No fields provided" in resp.json()["detail"]

    async def test_update_sensor_change_type(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        sensor = _make_sensor(name="Sensor", sensor_type=SensorType.multisensor)
        sensor.id = SENSOR_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(sensor)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = await client.put(
            f"/api/v1/sensors/{SENSOR_ID_1}",
            json={"type": "temp_only"},
        )

        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()


# ===================================================================
# DELETE /api/v1/sensors/{sensor_id}
# ===================================================================


class TestDeleteSensor:
    async def test_delete_sensor_exists(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        sensor = _make_sensor(name="To Delete")
        sensor.id = SENSOR_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(sensor)
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        resp = await client.delete(f"/api/v1/sensors/{SENSOR_ID_1}")

        assert resp.status_code == 204
        mock_db.delete.assert_awaited_once_with(sensor)
        mock_db.commit.assert_awaited_once()

    async def test_delete_sensor_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.delete(f"/api/v1/sensors/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]
