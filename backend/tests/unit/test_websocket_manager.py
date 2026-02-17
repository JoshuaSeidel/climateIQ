"""Tests for backend.api.websocket — ConnectionManager."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.websocket import ConnectionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_ws() -> AsyncMock:
    """Create a mock WebSocket with the standard interface."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager() -> ConnectionManager:
    """ConnectionManager with Redis disabled."""
    mgr = ConnectionManager(redis_url="redis://localhost:6379/0")
    return mgr


# ===================================================================
# connect
# ===================================================================


class TestConnect:
    """Tests for accepting and tracking WebSocket connections."""

    async def test_connect_accepts_websocket(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws)

        ws.accept.assert_awaited_once()
        assert ws in manager._connections.get("general", set())

    async def test_connect_adds_to_set(self, manager: ConnectionManager) -> None:
        ws1 = _mock_ws()
        ws2 = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws1)
            await manager.connect(ws2)

        assert manager.get_connection_count() == 2
        assert ws1 in manager._connections.get("general", set())
        assert ws2 in manager._connections.get("general", set())


# ===================================================================
# disconnect_client
# ===================================================================


class TestDisconnectClient:
    """Tests for removing and closing WebSocket connections."""

    async def test_disconnect_removes_from_set(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws)
        assert ws in manager._connections.get("general", set())

        await manager.disconnect_client(ws)
        assert ws not in manager._connections.get("general", set())

    async def test_disconnect_closes_websocket(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws)

        await manager.disconnect_client(ws)
        ws.close.assert_awaited_once()

    async def test_disconnect_nonexistent_is_safe(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        # Should not raise
        await manager.disconnect_client(ws)


# ===================================================================
# get_connection_count
# ===================================================================


class TestGetConnectionCount:
    """Tests for the connection counter."""

    async def test_zero_initially(self, manager: ConnectionManager) -> None:
        assert manager.get_connection_count() == 0

    async def test_increments_on_connect(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws)
        assert manager.get_connection_count() == 1

    async def test_decrements_on_disconnect(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws)
        await manager.disconnect_client(ws)
        assert manager.get_connection_count() == 0


# ===================================================================
# cleanup_stale
# ===================================================================


class TestCleanupStale:
    """Tests for removing unresponsive connections."""

    async def test_removes_connections_that_fail_ping(self, manager: ConnectionManager) -> None:
        good_ws = _mock_ws()
        bad_ws = _mock_ws()
        bad_ws.send_json.side_effect = ConnectionError("gone")

        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(good_ws)
            await manager.connect(bad_ws)

        assert manager.get_connection_count() == 2

        removed = await manager.cleanup_stale()

        assert removed == 1
        assert good_ws in manager._connections.get("general", set())
        assert bad_ws not in manager._connections.get("general", set())

    async def test_no_stale_connections(self, manager: ConnectionManager) -> None:
        ws = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws)

        removed = await manager.cleanup_stale()
        assert removed == 0
        assert manager.get_connection_count() == 1


# ===================================================================
# broadcast
# ===================================================================


class TestBroadcast:
    """Tests for broadcasting messages to all clients."""

    async def test_broadcast_sends_to_all(self, manager: ConnectionManager) -> None:
        ws1 = _mock_ws()
        ws2 = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws1)
            await manager.connect(ws2)

        message = {"type": "test", "data": "hello"}
        with patch.object(manager, "publish_redis", new_callable=AsyncMock):
            await manager.broadcast(message)

        expected_payload = json.dumps(message, default=str)
        ws1.send_text.assert_awaited_once_with(expected_payload)
        ws2.send_text.assert_awaited_once_with(expected_payload)


# ===================================================================
# _send_local
# ===================================================================


class TestSendLocal:
    """Tests for the local fan-out method."""

    async def test_sends_to_all_connections(self, manager: ConnectionManager) -> None:
        ws1 = _mock_ws()
        ws2 = _mock_ws()
        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(ws1)
            await manager.connect(ws2)

        message = {"type": "update", "value": 42}
        await manager._send_local(message)

        expected = json.dumps(message, default=str)
        ws1.send_text.assert_awaited_once_with(expected)
        ws2.send_text.assert_awaited_once_with(expected)

    async def test_removes_failed_connections(self, manager: ConnectionManager) -> None:
        good_ws = _mock_ws()
        bad_ws = _mock_ws()
        bad_ws.send_text.side_effect = ConnectionError("broken pipe")

        with patch.object(manager, "_ensure_redis", new_callable=AsyncMock, return_value=None):
            await manager.connect(good_ws)
            await manager.connect(bad_ws)

        await manager._send_local({"type": "test"})

        assert good_ws in manager._connections.get("general", set())
        assert bad_ws not in manager._connections.get("general", set())

    async def test_no_clients_is_noop(self, manager: ConnectionManager) -> None:
        # Should not raise
        await manager._send_local({"type": "test"})


# ===================================================================
# Sensor callbacks
# ===================================================================


class TestSensorCallbacks:
    """Tests for sensor callback registration."""

    def test_register_callback(self, manager: ConnectionManager) -> None:
        cb = MagicMock()
        manager.register_sensor_callback(cb)
        assert cb in manager._sensor_callbacks

    def test_register_no_duplicates(self, manager: ConnectionManager) -> None:
        cb = MagicMock()
        manager.register_sensor_callback(cb)
        manager.register_sensor_callback(cb)
        assert len(manager._sensor_callbacks) == 1

    def test_unregister_callback(self, manager: ConnectionManager) -> None:
        cb = MagicMock()
        manager.register_sensor_callback(cb)
        manager.unregister_sensor_callback(cb)
        assert cb not in manager._sensor_callbacks

    def test_unregister_nonexistent_is_safe(self, manager: ConnectionManager) -> None:
        cb = MagicMock()
        # Should not raise
        manager.unregister_sensor_callback(cb)


# ===================================================================
# _serialize_sensor_payload
# ===================================================================


class TestSerializeSensorPayload:
    """Tests for the sensor payload serializer."""

    def test_correct_format(self, manager: ConnectionManager) -> None:
        payload = manager._serialize_sensor_payload(
            "sensor-123",
            {"temperature": 22.5, "humidity": 55.0},
        )

        assert payload["type"] == "sensor_update"
        assert payload["sensor_id"] == "sensor-123"
        assert "timestamp" in payload
        assert payload["data"]["temperature"] == 22.5
        assert payload["data"]["humidity"] == 55.0

    def test_none_sensor_id(self, manager: ConnectionManager) -> None:
        payload = manager._serialize_sensor_payload(
            None,
            {"temperature": 20.0},
        )
        assert payload["sensor_id"] is None

    def test_timestamp_is_iso_format(self, manager: ConnectionManager) -> None:
        payload = manager._serialize_sensor_payload("s1", {"temp": 1})
        ts = payload["timestamp"]
        assert isinstance(ts, str)
        # Should be parseable as ISO datetime
        assert "T" in ts
