"""Tests for backend.integrations.ha_websocket — state parsing, callbacks, URL conversion."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.integrations.ha_websocket import HAStateChange, HAWebSocketClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> HAWebSocketClient:
    return HAWebSocketClient("http://localhost:8123", "fake-token")


# ===================================================================
# HAStateChange dataclass
# ===================================================================


class TestHAStateChange:
    """Tests for the HAStateChange data model defaults."""

    def test_default_values(self) -> None:
        change = HAStateChange(entity_id="sensor.temp", domain="sensor", state="22.5")
        assert change.entity_id == "sensor.temp"
        assert change.domain == "sensor"
        assert change.state == "22.5"
        assert change.attributes == {}
        assert change.temperature is None
        assert change.humidity is None
        assert change.pressure is None
        assert change.lux is None
        assert change.presence is None
        assert change.last_changed == ""
        assert change.last_updated == ""
        assert isinstance(change.timestamp, datetime)

    def test_fields_set_properly(self) -> None:
        now = datetime.now(UTC)
        change = HAStateChange(
            entity_id="binary_sensor.motion",
            domain="binary_sensor",
            state="on",
            attributes={"device_class": "motion"},
            temperature=23.5,
            humidity=55.0,
            pressure=1013.0,
            lux=400.0,
            presence=True,
            last_changed="2026-01-01T00:00:00Z",
            last_updated="2026-01-01T00:00:01Z",
            timestamp=now,
        )
        assert change.entity_id == "binary_sensor.motion"
        assert change.domain == "binary_sensor"
        assert change.state == "on"
        assert change.temperature == 23.5
        assert change.humidity == 55.0
        assert change.pressure == 1013.0
        assert change.lux == 400.0
        assert change.presence is True
        assert change.last_changed == "2026-01-01T00:00:00Z"
        assert change.timestamp == now


# ===================================================================
# URL conversion
# ===================================================================


class TestURLConversion:
    """Tests for HTTP → WS URL conversion in the constructor."""

    def test_http_to_ws(self) -> None:
        c = HAWebSocketClient("http://homeassistant.local:8123", "token")
        assert c._ws_url == "ws://homeassistant.local:8123/api/websocket"

    def test_https_to_wss(self) -> None:
        c = HAWebSocketClient("https://ha.example.com", "token")
        assert c._ws_url == "wss://ha.example.com/api/websocket"

    def test_bare_host(self) -> None:
        c = HAWebSocketClient("homeassistant.local:8123", "token")
        assert c._ws_url == "ws://homeassistant.local:8123/api/websocket"

    def test_trailing_slash_stripped(self) -> None:
        c = HAWebSocketClient("http://ha.local:8123/", "token")
        assert c._ws_url == "ws://ha.local:8123/api/websocket"


# ===================================================================
# Callbacks — add / remove
# ===================================================================


class TestCallbacks:
    """Tests for callback registration and removal."""

    def test_add_callback(self, client: HAWebSocketClient) -> None:
        cb = AsyncMock()
        client.add_callback(cb)
        assert cb in client._callbacks
        assert len(client._callbacks) == 1

    def test_add_callback_no_duplicates(self, client: HAWebSocketClient) -> None:
        cb = AsyncMock()
        client.add_callback(cb)
        client.add_callback(cb)
        assert len(client._callbacks) == 1

    def test_remove_callback(self, client: HAWebSocketClient) -> None:
        cb = AsyncMock()
        client.add_callback(cb)
        client.remove_callback(cb)
        assert cb not in client._callbacks
        assert len(client._callbacks) == 0

    def test_remove_nonexistent_callback_no_error(self, client: HAWebSocketClient) -> None:
        cb = AsyncMock()
        # Should not raise
        client.remove_callback(cb)


# ===================================================================
# connected property
# ===================================================================


class TestConnectedProperty:
    """Tests for the connected property reflecting _connected event state."""

    def test_initially_not_connected(self, client: HAWebSocketClient) -> None:
        assert client.connected is False

    def test_connected_after_event_set(self, client: HAWebSocketClient) -> None:
        client._connected.set()
        assert client.connected is True

    def test_disconnected_after_event_clear(self, client: HAWebSocketClient) -> None:
        client._connected.set()
        client._connected.clear()
        assert client.connected is False


# ===================================================================
# _parse_state_change
# ===================================================================


class TestParseStateChange:
    """Tests for the state change parser."""

    def test_sensor_temperature_from_state(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "sensor.living_room_temperature",
            "sensor",
            {
                "state": "22.5",
                "attributes": {"unit_of_measurement": "°C"},
            },
        )
        assert change is not None
        assert change.entity_id == "sensor.living_room_temperature"
        assert change.domain == "sensor"
        assert change.state == "22.5"
        assert change.temperature == 22.5

    def test_sensor_humidity_from_attributes(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "sensor.bathroom_climate",
            "sensor",
            {
                "state": "23.1",
                "attributes": {
                    "humidity": 65.2,
                    "unit_of_measurement": "°C",
                },
            },
        )
        assert change is not None
        assert change.humidity == 65.2

    def test_binary_sensor_presence_on(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "binary_sensor.hallway_motion",
            "binary_sensor",
            {
                "state": "on",
                "attributes": {"device_class": "motion"},
            },
        )
        assert change is not None
        assert change.presence is True

    def test_binary_sensor_presence_off(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "binary_sensor.hallway_occupancy",
            "binary_sensor",
            {
                "state": "off",
                "attributes": {"device_class": "occupancy"},
            },
        )
        assert change is not None
        assert change.presence is False

    def test_climate_domain_temperature_from_attributes(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "climate.living_room",
            "climate",
            {
                "state": "heat",
                "attributes": {
                    "current_temperature": 21.0,
                    "current_humidity": 50.0,
                    "temperature": 22.0,
                },
            },
        )
        assert change is not None
        assert change.temperature == 22.0
        assert change.humidity == 50.0

    def test_last_changed_and_updated(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "sensor.test",
            "sensor",
            {
                "state": "10",
                "attributes": {},
                "last_changed": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:01Z",
            },
        )
        assert change is not None
        assert change.last_changed == "2026-01-01T00:00:00Z"
        assert change.last_updated == "2026-01-01T00:00:01Z"

    def test_pressure_from_attributes(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "sensor.weather_station",
            "sensor",
            {
                "state": "1013",
                "attributes": {"pressure": 1013.25},
            },
        )
        assert change is not None
        assert change.pressure == 1013.25

    def test_lux_from_attributes(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "sensor.light_level",
            "sensor",
            {
                "state": "450",
                "attributes": {"illuminance": 450.0},
            },
        )
        assert change is not None
        assert change.lux == 450.0

    def test_non_numeric_state_no_crash(self, client: HAWebSocketClient) -> None:
        change = client._parse_state_change(
            "sensor.temperature_status",
            "sensor",
            {
                "state": "unavailable",
                "attributes": {},
            },
        )
        assert change is not None
        assert change.state == "unavailable"
        # temperature should remain None since "unavailable" is not numeric
        # (the entity_id contains "temperature" so it tries to parse)
        assert change.temperature is None


# ===================================================================
# _dispatch
# ===================================================================


class TestDispatch:
    """Tests for callback dispatching."""

    def test_calls_all_registered_callbacks(self, client: HAWebSocketClient) -> None:
        cb1 = MagicMock()
        cb2 = MagicMock()
        client.add_callback(cb1)
        client.add_callback(cb2)

        change = HAStateChange(entity_id="sensor.test", domain="sensor", state="20")
        client._dispatch(change)

        cb1.assert_called_once_with(change)
        cb2.assert_called_once_with(change)

    def test_exception_in_callback_does_not_stop_others(self, client: HAWebSocketClient) -> None:
        cb1 = MagicMock(side_effect=RuntimeError("boom"))
        cb2 = MagicMock()
        client.add_callback(cb1)
        client.add_callback(cb2)

        change = HAStateChange(entity_id="sensor.test", domain="sensor", state="20")
        # Should not raise
        client._dispatch(change)

        cb1.assert_called_once_with(change)
        cb2.assert_called_once_with(change)


# ===================================================================
# Entity filter
# ===================================================================


class TestEntityFilter:
    """Tests for the optional entity_filter parameter."""

    def test_filter_set_on_init(self) -> None:
        entities = {"sensor.temp", "sensor.humidity"}
        c = HAWebSocketClient("http://ha:8123", "token", entity_filter=entities)
        assert c._entity_filter == entities

    def test_no_filter_by_default(self) -> None:
        c = HAWebSocketClient("http://ha:8123", "token")
        assert c._entity_filter is None
