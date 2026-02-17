"""Integration tests for device API routes (/api/v1/devices).

Uses mocked database dependencies so tests run without a live database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from backend.api.dependencies import get_db
from backend.api.main import app
from backend.models.database import Device, DeviceAction, Zone
from backend.models.enums import (
    ActionType,
    ControlMethod,
    DeviceType,
    TriggerType,
)
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------

ZONE_ID = uuid4()
DEVICE_ID_1 = uuid4()
DEVICE_ID_2 = uuid4()
NOW = datetime.now(UTC)


def _make_device(
    *,
    device_id: None = None,
    name: str = "Main Thermostat",
    device_type: DeviceType = DeviceType.thermostat,
    control_method: ControlMethod = ControlMethod.ha_service_call,
    zone_id: None = None,
    is_primary: bool = True,
) -> MagicMock:
    """Return a MagicMock that behaves like a Device ORM instance."""
    device = MagicMock(spec=Device)
    device.id = device_id or uuid4()
    device.zone_id = zone_id or ZONE_ID
    device.name = name
    device.type = device_type
    device.manufacturer = None
    device.model = None
    device.ha_entity_id = None
    device.control_method = control_method
    device.capabilities = {}
    device.is_primary = is_primary
    device.constraints = {}
    device.created_at = NOW
    return device


def _make_zone_mock() -> MagicMock:
    """Return a minimal Zone mock for zone-existence checks."""
    zone = MagicMock(spec=Zone)
    zone.id = ZONE_ID
    zone.name = "Test Zone"
    return zone


def _make_device_action(
    *,
    device_id: None = None,
    action_type: ActionType = ActionType.set_temperature,
    triggered_by: TriggerType = TriggerType.user_override,
    parameters: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a MagicMock that behaves like a DeviceAction ORM instance."""
    action = MagicMock(spec=DeviceAction)
    action.id = uuid4()
    action.device_id = device_id or DEVICE_ID_1
    action.zone_id = None
    action.triggered_by = triggered_by
    action.action_type = action_type
    action.parameters = parameters or {}
    action.result = result or {"success": True}
    action.reasoning = None
    action.mode = None
    action.created_at = NOW
    return action


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
# GET /api/v1/devices
# ===================================================================


class TestListDevices:
    async def test_list_devices_empty(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalars_all([])

        resp = await client.get("/api/v1/devices")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_devices_with_data(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        d1 = _make_device(name="Thermostat A")
        d2 = _make_device(name="Smart Vent B", device_type=DeviceType.smart_vent)
        mock_db.execute.return_value = _scalars_all([d1, d2])

        resp = await client.get("/api/v1/devices")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Thermostat A"
        assert data[1]["name"] == "Smart Vent B"

    async def test_list_devices_filter_by_zone_id(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        d1 = _make_device(name="Zone Device")
        mock_db.execute.return_value = _scalars_all([d1])

        resp = await client.get("/api/v1/devices", params={"zone_id": str(ZONE_ID)})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["zone_id"] == str(ZONE_ID)

    async def test_list_devices_filter_by_type(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        d1 = _make_device(name="Fan", device_type=DeviceType.fan)
        mock_db.execute.return_value = _scalars_all([d1])

        resp = await client.get("/api/v1/devices", params={"type": "fan"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "fan"

    async def test_list_devices_filter_by_is_primary(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        d1 = _make_device(name="Primary", is_primary=True)
        mock_db.execute.return_value = _scalars_all([d1])

        resp = await client.get("/api/v1/devices", params={"is_primary": "true"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["is_primary"] is True


# ===================================================================
# GET /api/v1/devices/{device_id}
# ===================================================================


class TestGetDevice:
    async def test_get_device_exists(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        device = _make_device(name="Office Thermostat")
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)

        resp = await client.get(f"/api/v1/devices/{DEVICE_ID_1}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Office Thermostat"
        assert data["id"] == str(DEVICE_ID_1)

    async def test_get_device_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.get(f"/api/v1/devices/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]


# ===================================================================
# POST /api/v1/devices
# ===================================================================


class TestCreateDevice:
    async def test_create_device_valid(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        zone_mock = _make_zone_mock()
        created_device = _make_device(
            name="New Thermostat",
            device_type=DeviceType.thermostat,
            control_method=ControlMethod.ha_service_call,
        )

        mock_db.execute = AsyncMock(return_value=_scalar_one_or_none(zone_mock))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch("backend.api.routes.devices.Device", return_value=created_device):
            resp = await client.post(
                "/api/v1/devices",
                json={
                    "name": "New Thermostat",
                    "type": "thermostat",
                    "zone_id": str(ZONE_ID),
                    "control_method": "ha_service_call",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Thermostat"
        assert data["type"] == "thermostat"
        assert data["control_method"] == "ha_service_call"
        mock_db.add.assert_called_once_with(created_device)
        mock_db.commit.assert_awaited_once()

    async def test_create_device_zone_not_found(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_zone = uuid4()

        resp = await client.post(
            "/api/v1/devices",
            json={
                "name": "Orphan Device",
                "type": "thermostat",
                "zone_id": str(missing_zone),
                "control_method": "ha_service_call",
            },
        )

        assert resp.status_code == 404
        assert str(missing_zone) in resp.json()["detail"]

    async def test_create_device_missing_name_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/devices",
            json={
                "type": "thermostat",
                "zone_id": str(ZONE_ID),
                "control_method": "ha_service_call",
            },
        )
        assert resp.status_code == 422

    async def test_create_device_missing_control_method_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/devices",
            json={
                "name": "Test",
                "type": "thermostat",
                "zone_id": str(ZONE_ID),
            },
        )
        assert resp.status_code == 422

    async def test_create_device_invalid_type_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.post(
            "/api/v1/devices",
            json={
                "name": "Test",
                "type": "nonexistent_device",
                "zone_id": str(ZONE_ID),
                "control_method": "ha_service_call",
            },
        )
        assert resp.status_code == 422


# ===================================================================
# PUT /api/v1/devices/{device_id}
# ===================================================================


class TestUpdateDevice:
    async def test_update_device_valid(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        device = _make_device(name="Old Name")
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = await client.put(
            f"/api/v1/devices/{DEVICE_ID_1}",
            json={"name": "Updated Name"},
        )

        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()

    async def test_update_device_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.put(
            f"/api/v1/devices/{missing_id}",
            json={"name": "Anything"},
        )

        assert resp.status_code == 404

    async def test_update_device_empty_body_returns_400(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        device = _make_device(name="Existing")
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)

        resp = await client.put(f"/api/v1/devices/{DEVICE_ID_1}", json={})

        assert resp.status_code == 400
        assert "No fields provided" in resp.json()["detail"]

    async def test_update_device_change_control_method(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        device = _make_device(name="Device", control_method=ControlMethod.ha_service_call)
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = await client.put(
            f"/api/v1/devices/{DEVICE_ID_1}",
            json={"control_method": "ha_service_call"},
        )

        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()


# ===================================================================
# DELETE /api/v1/devices/{device_id}
# ===================================================================


class TestDeleteDevice:
    async def test_delete_device_exists(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        device = _make_device(name="To Delete")
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        resp = await client.delete(f"/api/v1/devices/{DEVICE_ID_1}")

        assert resp.status_code == 204
        mock_db.delete.assert_awaited_once_with(device)
        mock_db.commit.assert_awaited_once()

    async def test_delete_device_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.delete(f"/api/v1/devices/{missing_id}")

        assert resp.status_code == 404
        assert str(missing_id) in resp.json()["detail"]


# ===================================================================
# POST /api/v1/devices/{device_id}/action
# ===================================================================


class TestDeviceAction:
    """Tests for POST /api/v1/devices/{device_id}/action.

    All tests that pass validation mock ``_dispatch_action`` to avoid
    real network calls to Home Assistant or MQTT brokers.
    """

    async def test_action_set_temperature_thermostat(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """Valid set_temperature action on a thermostat should succeed."""
        device = _make_device(
            name="Thermostat",
            device_type=DeviceType.thermostat,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1

        action_record = _make_device_action(
            action_type=ActionType.set_temperature,
            parameters={"temperature": 22},
            result={"success": True},
        )

        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "backend.api.routes.devices._dispatch_action",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "backend.api.routes.devices.DeviceAction",
                return_value=action_record,
            ),
        ):
            resp = await client.post(
                f"/api/v1/devices/{DEVICE_ID_1}/action",
                json={
                    "action_type": "set_temperature",
                    "triggered_by": "user_override",
                    "parameters": {"temperature": 22},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action_type"] == "set_temperature"
        assert data["triggered_by"] == "user_override"
        mock_db.add.assert_called_once_with(action_record)
        mock_db.commit.assert_awaited_once()

    async def test_action_turn_on_fan(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        """Valid turn_on action on a fan should succeed."""
        device = _make_device(
            name="Ceiling Fan",
            device_type=DeviceType.fan,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1

        action_record = _make_device_action(
            action_type=ActionType.turn_on,
            result={"success": True},
        )

        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "backend.api.routes.devices._dispatch_action",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "backend.api.routes.devices.DeviceAction",
                return_value=action_record,
            ),
        ):
            resp = await client.post(
                f"/api/v1/devices/{DEVICE_ID_1}/action",
                json={
                    "action_type": "turn_on",
                    "triggered_by": "schedule",
                    "parameters": {},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action_type"] == "turn_on"

    async def test_action_invalid_for_device_type_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """set_temperature on a fan should return 422."""
        device = _make_device(
            name="Fan",
            device_type=DeviceType.fan,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)

        resp = await client.post(
            f"/api/v1/devices/{DEVICE_ID_1}/action",
            json={
                "action_type": "set_temperature",
                "triggered_by": "user_override",
                "parameters": {"temperature": 22},
            },
        )

        assert resp.status_code == 422
        assert "not valid for device type" in resp.json()["detail"]

    async def test_action_set_vent_position_on_thermostat_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """set_vent_position on a thermostat should return 422."""
        device = _make_device(
            name="Thermostat",
            device_type=DeviceType.thermostat,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)

        resp = await client.post(
            f"/api/v1/devices/{DEVICE_ID_1}/action",
            json={
                "action_type": "set_vent_position",
                "triggered_by": "user_override",
                "parameters": {"position": 50},
            },
        )

        assert resp.status_code == 422
        assert "not valid for device type" in resp.json()["detail"]

    async def test_action_open_cover_on_blind(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """open_cover on a blind should succeed."""
        device = _make_device(
            name="Window Blind",
            device_type=DeviceType.blind,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1

        action_record = _make_device_action(
            action_type=ActionType.open_cover,
            triggered_by=TriggerType.llm_decision,
            result={"success": True},
        )

        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "backend.api.routes.devices._dispatch_action",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "backend.api.routes.devices.DeviceAction",
                return_value=action_record,
            ),
        ):
            resp = await client.post(
                f"/api/v1/devices/{DEVICE_ID_1}/action",
                json={
                    "action_type": "open_cover",
                    "triggered_by": "llm_decision",
                    "parameters": {},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action_type"] == "open_cover"
        assert data["triggered_by"] == "llm_decision"

    async def test_action_device_not_found(self, client: AsyncClient, mock_db: AsyncMock) -> None:
        mock_db.execute.return_value = _scalar_one_or_none(None)
        missing_id = uuid4()

        resp = await client.post(
            f"/api/v1/devices/{missing_id}/action",
            json={
                "action_type": "turn_on",
                "triggered_by": "user_override",
                "parameters": {},
            },
        )

        assert resp.status_code == 404

    async def test_action_set_mode_on_mini_split(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """set_mode on a mini_split should succeed."""
        device = _make_device(
            name="Mini Split",
            device_type=DeviceType.mini_split,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1

        action_record = _make_device_action(
            action_type=ActionType.set_mode,
            parameters={"mode": "cool"},
            result={"success": True},
        )

        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "backend.api.routes.devices._dispatch_action",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "backend.api.routes.devices.DeviceAction",
                return_value=action_record,
            ),
        ):
            resp = await client.post(
                f"/api/v1/devices/{DEVICE_ID_1}/action",
                json={
                    "action_type": "set_mode",
                    "triggered_by": "rule_engine",
                    "parameters": {"mode": "cool"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action_type"] == "set_mode"

    async def test_action_set_vent_position_on_smart_vent(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """set_vent_position on a smart_vent should succeed."""
        device = _make_device(
            name="Smart Vent",
            device_type=DeviceType.smart_vent,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1

        action_record = _make_device_action(
            action_type=ActionType.set_vent_position,
            parameters={"position": 75},
            result={"success": True},
        )

        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "backend.api.routes.devices._dispatch_action",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "backend.api.routes.devices.DeviceAction",
                return_value=action_record,
            ),
        ):
            resp = await client.post(
                f"/api/v1/devices/{DEVICE_ID_1}/action",
                json={
                    "action_type": "set_vent_position",
                    "triggered_by": "comfort_correction",
                    "parameters": {"position": 75},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action_type"] == "set_vent_position"

    async def test_action_close_cover_on_fan_returns_422(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """close_cover on a fan should return 422."""
        device = _make_device(
            name="Fan",
            device_type=DeviceType.fan,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1
        mock_db.execute.return_value = _scalar_one_or_none(device)

        resp = await client.post(
            f"/api/v1/devices/{DEVICE_ID_1}/action",
            json={
                "action_type": "close_cover",
                "triggered_by": "user_override",
                "parameters": {},
            },
        )

        assert resp.status_code == 422
        assert "not valid for device type" in resp.json()["detail"]
        assert "fan" in resp.json()["detail"]

    async def test_action_dispatch_failure_still_records(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        """When dispatch raises an exception, the action is still recorded."""
        device = _make_device(
            name="Thermostat",
            device_type=DeviceType.thermostat,
            control_method=ControlMethod.ha_service_call,
        )
        device.id = DEVICE_ID_1

        action_record = _make_device_action(
            action_type=ActionType.turn_off,
            result={"success": False, "error": "Connection refused"},
        )

        mock_db.execute.return_value = _scalar_one_or_none(device)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "backend.api.routes.devices._dispatch_action",
                new_callable=AsyncMock,
                side_effect=ConnectionError("Connection refused"),
            ),
            patch(
                "backend.api.routes.devices.DeviceAction",
                return_value=action_record,
            ),
        ):
            resp = await client.post(
                f"/api/v1/devices/{DEVICE_ID_1}/action",
                json={
                    "action_type": "turn_off",
                    "triggered_by": "user_override",
                    "parameters": {},
                },
            )

        # Action should still be recorded even though dispatch failed
        assert resp.status_code == 200
        mock_db.add.assert_called_once_with(action_record)
        mock_db.commit.assert_awaited_once()
