"""Home Assistant WebSocket client for real-time entity state subscriptions.

This is the PRIMARY sensor data ingestion path for ClimateIQ. It subscribes
to HA's state_changed events via WebSocket and dispatches normalized readings
to registered callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

# Domains we care about for sensor ingestion
_SENSOR_DOMAINS = frozenset({"sensor", "binary_sensor", "climate", "fan", "cover", "switch"})

# Attributes we try to extract from HA entity state
_NUMERIC_ATTRS = {
    "temperature": ["temperature", "current_temperature"],
    "humidity": ["humidity", "current_humidity"],
    "pressure": ["pressure"],
    "lux": ["illuminance", "illuminance_lux"],
}
_BOOL_ATTRS = {
    "presence": ["occupancy", "motion", "presence"],
}


@dataclass(slots=True)
class HAStateChange:
    """Normalized state change event from Home Assistant."""

    entity_id: str
    domain: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    lux: float | None = None
    presence: bool | None = None
    last_changed: str = ""
    last_updated: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


StateChangeCallback = Callable[[HAStateChange], Awaitable[None] | None]


class HAWebSocketError(Exception):
    """Base exception for HA WebSocket errors."""


class HAWebSocketAuthError(HAWebSocketError):
    """Authentication failed."""


class HAWebSocketClient:
    """Async WebSocket client for Home Assistant real-time state subscriptions.

    Usage::

        client = HAWebSocketClient("http://homeassistant.local:8123", token="ey...")
        client.add_callback(my_handler)
        await client.connect()
        # ... client runs in background, dispatching state changes ...
        await client.disconnect()
    """

    _RECONNECT_DELAYS = (1, 2, 5, 10, 30, 60)

    def __init__(
        self,
        url: str,
        token: str,
        *,
        entity_filter: set[str] | None = None,
    ) -> None:
        # Convert HTTP URL to WebSocket URL
        base = url.rstrip("/")
        if base.startswith("https://"):
            self._ws_url = base.replace("https://", "wss://", 1) + "/api/websocket"
        elif base.startswith("http://"):
            self._ws_url = base.replace("http://", "ws://", 1) + "/api/websocket"
        else:
            self._ws_url = f"ws://{base}/api/websocket"

        self._token = token
        self._entity_filter = entity_filter  # If set, only these entity_ids
        self._ws: ClientConnection | None = None
        self._connected = asyncio.Event()
        self._stop = False
        self._lock = asyncio.Lock()
        self._listen_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._msg_id = 0
        self._callbacks: list[StateChangeCallback] = []
        self._entities_seen: set[str] = set()
        self._pending_commands: dict[int, asyncio.Future[Any]] = {}

    # ------------------------------------------------------------------
    # Public API — lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect, authenticate, and subscribe to state_changed events."""
        async with self._lock:
            if self._ws is not None:
                return

            await self._open_and_auth()
            self._stop = False
            self._listen_task = asyncio.create_task(self._listen_loop(), name="ha-ws-listen")
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(), name="ha-ws-reconnect"
            )
            logger.info("HA WebSocket connected to %s", self._ws_url)

    async def disconnect(self) -> None:
        """Cleanly shut down the WebSocket connection and background tasks."""
        async with self._lock:
            self._stop = True
            self._connected.clear()
            await self._cancel_tasks()
            if self._ws is not None:
                with suppress(Exception):
                    await self._ws.close()
                self._ws = None
            logger.info("HA WebSocket disconnected")

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------
    # Public API — callbacks
    # ------------------------------------------------------------------

    def add_callback(self, callback: StateChangeCallback) -> None:
        """Register a callback invoked for every relevant state change."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def remove_callback(self, callback: StateChangeCallback) -> None:
        """Unregister a previously registered callback."""
        with suppress(ValueError):
            self._callbacks.remove(callback)

    def add_entity_to_filter(self, entity_id: str) -> None:
        """Dynamically add an entity to the filter (for newly created sensors)."""
        if self._entity_filter is not None:
            self._entity_filter.add(entity_id)

    async def send_command(
        self,
        msg_type: str,
        cmd_timeout: float = 30.0,
        **kwargs: Any,
    ) -> Any:
        """Send a WS command and wait for the response.

        Works for HA WebSocket commands like ``config/device_registry/list``
        and ``config/entity_registry/list``.

        Returns the ``result`` field from the response message.

        Raises:
            HAWebSocketError: If not connected, send fails, or response
                indicates failure.
            TimeoutError: If no response arrives within *cmd_timeout* seconds.
        """
        if self._ws is None or not self._connected.is_set():
            raise HAWebSocketError("WebSocket is not connected")

        self._msg_id += 1
        cmd_id = self._msg_id
        payload: dict[str, Any] = {"id": cmd_id, "type": msg_type, **kwargs}

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_commands[cmd_id] = future

        try:
            await self._ws.send(json.dumps(payload))
            result = await asyncio.wait_for(future, timeout=cmd_timeout)
        except TimeoutError:
            self._pending_commands.pop(cmd_id, None)
            raise HAWebSocketError(
                f"Timeout waiting for response to {msg_type} (id={cmd_id})"
            ) from None
        except Exception:
            self._pending_commands.pop(cmd_id, None)
            raise

        return result

    # ------------------------------------------------------------------
    # Internal — connection & auth
    # ------------------------------------------------------------------

    async def _open_and_auth(self) -> None:
        """Open a WebSocket connection and perform HA authentication."""
        try:
            self._ws = await websockets.connect(
                self._ws_url,
                additional_headers={"User-Agent": "ClimateIQ/1.0"},
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
        except Exception as exc:
            logger.error("Failed to connect to HA WebSocket at %s: %s", self._ws_url, exc)
            raise HAWebSocketError(f"Connection failed: {exc}") from exc

        # Wait for auth_required
        raw = await self._ws.recv()
        msg = json.loads(raw)
        if msg.get("type") != "auth_required":
            raise HAWebSocketError(f"Expected auth_required, got: {msg.get('type')}")

        # Send auth
        await self._ws.send(
            json.dumps(
                {
                    "type": "auth",
                    "access_token": self._token,
                }
            )
        )

        # Wait for auth response
        raw = await self._ws.recv()
        msg = json.loads(raw)
        if msg.get("type") == "auth_invalid":
            raise HAWebSocketAuthError(
                f"Authentication failed: {msg.get('message', 'invalid token')}"
            )
        if msg.get("type") != "auth_ok":
            raise HAWebSocketError(f"Expected auth_ok, got: {msg.get('type')}")

        logger.info("HA WebSocket authenticated (HA version: %s)", msg.get("ha_version", "?"))

        # Subscribe to state_changed events
        self._msg_id += 1
        await self._ws.send(
            json.dumps(
                {
                    "id": self._msg_id,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }
            )
        )

        raw = await self._ws.recv()
        result = json.loads(raw)
        if not result.get("success", False):
            raise HAWebSocketError(f"Failed to subscribe to state_changed: {result}")

        self._connected.set()
        logger.info("Subscribed to state_changed events (msg_id=%d)", self._msg_id)

    # ------------------------------------------------------------------
    # Internal — message processing
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Continuously receive messages and dispatch state changes."""
        try:
            while not self._stop and self._ws is not None:
                try:
                    raw = await self._ws.recv()
                except websockets.ConnectionClosed:
                    logger.warning("HA WebSocket connection closed")
                    self._connected.clear()
                    break
                except Exception as exc:
                    logger.error("HA WebSocket recv error: %s", exc)
                    self._connected.clear()
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Route command responses to pending futures
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending_commands:
                    fut = self._pending_commands.pop(msg_id)
                    if not fut.done():
                        if msg.get("success", False):
                            fut.set_result(msg.get("result"))
                        else:
                            error = msg.get("error", {})
                            fut.set_exception(
                                HAWebSocketError(
                                    f"Command {msg_id} failed: "
                                    f"{error.get('message', 'unknown error')}"
                                )
                            )
                    continue

                if msg.get("type") != "event":
                    continue

                event = msg.get("event", {})
                if event.get("event_type") != "state_changed":
                    continue

                data = event.get("data", {})
                entity_id = data.get("entity_id", "")
                new_state = data.get("new_state")
                if not new_state or not entity_id:
                    continue

                # Domain filter
                domain = entity_id.split(".", 1)[0]
                if domain not in _SENSOR_DOMAINS:
                    continue

                # Entity filter (if configured)
                if self._entity_filter and entity_id not in self._entity_filter:
                    logger.debug("WS filter dropped entity %s (not in %d-entry filter)", entity_id, len(self._entity_filter))
                    continue

                # Track entities we've seen
                self._entities_seen.add(entity_id)

                # Parse into normalized state change
                change = self._parse_state_change(entity_id, domain, new_state)
                if change is not None:
                    self._dispatch(change)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("HA WebSocket listen loop crashed")
            self._connected.clear()
        finally:
            # Fail any pending command futures so callers don't hang until timeout
            self._fail_pending_commands("WebSocket connection lost")

    def _parse_state_change(
        self,
        entity_id: str,
        domain: str,
        state_data: dict[str, Any],
    ) -> HAStateChange | None:
        """Parse a raw HA state dict into a normalized HAStateChange."""
        state_val = str(state_data.get("state", ""))
        attrs = state_data.get("attributes", {})

        change = HAStateChange(
            entity_id=entity_id,
            domain=domain,
            state=state_val,
            attributes=attrs,
            last_changed=state_data.get("last_changed", ""),
            last_updated=state_data.get("last_updated", ""),
        )

        device_class = attrs.get("device_class", "")
        device_class = device_class.lower() if isinstance(device_class, str) else ""
        unit = attrs.get("unit_of_measurement", "") or attrs.get("temperature_unit", "") or ""

        # ----- Device-class / unit-based extraction (most reliable) -----
        _dc_matched = False

        if domain == "sensor":
            if device_class == "temperature":
                with suppress(ValueError, TypeError):
                    temp_val = float(state_val)
                    if unit == "°F":
                        temp_val = (temp_val - 32) * 5 / 9
                    change.temperature = temp_val
                    _dc_matched = True
            elif device_class == "humidity":
                with suppress(ValueError, TypeError):
                    change.humidity = float(state_val)
                    _dc_matched = True
            elif device_class == "illuminance":
                with suppress(ValueError, TypeError):
                    change.lux = float(state_val)
                    _dc_matched = True
            elif device_class == "pressure":
                with suppress(ValueError, TypeError):
                    change.pressure = float(state_val)
                    _dc_matched = True

        if domain == "binary_sensor" and device_class in ("occupancy", "motion", "presence"):
            change.presence = state_val.lower() in ("on", "true", "1")
            _dc_matched = True

        # ----- Fallback: keyword-in-entity-id extraction -----
        if not _dc_matched:
            # Extract numeric sensor values
            for field_name, attr_keys in _NUMERIC_ATTRS.items():
                # First check if the entity state itself is numeric (for sensor.* domain)
                if domain == "sensor" and field_name in entity_id.lower():
                    with suppress(ValueError, TypeError):
                        val_f = float(state_val)
                        # Convert F→C for temperature when unit indicates Fahrenheit
                        if field_name == "temperature" and unit == "°F":
                            val_f = (val_f - 32) * 5 / 9
                        setattr(change, field_name, val_f)
                        continue

                # Then check attributes
                for key in attr_keys:
                    val = attrs.get(key)
                    if val is not None:
                        with suppress(ValueError, TypeError):
                            val_f = float(val)
                            # Convert F→C for temperature from attributes
                            if field_name == "temperature" and unit == "°F":
                                val_f = (val_f - 32) * 5 / 9
                            setattr(change, field_name, val_f)
                            break

            # Extract boolean sensor values
            for field_name, attr_keys in _BOOL_ATTRS.items():
                # Binary sensors: state is on/off
                if domain == "binary_sensor":
                    for keyword in attr_keys:
                        if keyword in entity_id.lower():
                            change.presence = state_val.lower() in ("on", "true", "1")
                            break

                # Also check attributes
                for key in attr_keys:
                    val = attrs.get(key)
                    if val is not None:
                        if isinstance(val, bool):
                            setattr(change, field_name, val)
                        elif isinstance(val, str):
                            setattr(change, field_name, val.lower() in ("on", "true", "1"))
                        break

        return change

    def _dispatch(self, change: HAStateChange) -> None:
        """Invoke all registered callbacks with the state change."""
        for callback in list(self._callbacks):
            try:
                result = callback(change)
                if asyncio.iscoroutine(result):
                    task = asyncio.create_task(result)
                    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except Exception:
                logger.exception("HA WebSocket callback raised an exception")

    # ------------------------------------------------------------------
    # Internal — pending command management
    # ------------------------------------------------------------------

    def _fail_pending_commands(self, reason: str) -> None:
        """Reject all pending command futures so callers don't hang."""
        pending = dict(self._pending_commands)
        self._pending_commands.clear()
        for cmd_id, fut in pending.items():
            if not fut.done():
                fut.set_exception(HAWebSocketError(f"{reason} (cmd_id={cmd_id})"))

    # ------------------------------------------------------------------
    # Internal — reconnection
    # ------------------------------------------------------------------

    async def _reconnect_loop(self) -> None:
        """Monitor connection and auto-reconnect with backoff."""
        try:
            while not self._stop:
                await asyncio.sleep(2)
                if self._connected.is_set():
                    continue

                logger.info("HA WebSocket connection lost — starting reconnect")
                for delay in self._RECONNECT_DELAYS:
                    if self._stop:
                        return
                    try:
                        # Close old connection
                        if self._ws is not None:
                            with suppress(Exception):
                                await self._ws.close()
                            self._ws = None

                        await self._open_and_auth()

                        # Restart listen loop
                        if self._listen_task and not self._listen_task.done():
                            self._listen_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await self._listen_task
                        self._listen_task = asyncio.create_task(
                            self._listen_loop(), name="ha-ws-listen"
                        )

                        logger.info("HA WebSocket reconnected")
                        break
                    except HAWebSocketAuthError:
                        logger.error("HA WebSocket auth failed — not retrying")
                        return
                    except Exception as exc:
                        logger.warning(
                            "HA WebSocket reconnect failed (%s), retrying in %ss",
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                else:
                    logger.error(
                        "HA WebSocket reconnect exhausted backoff; retrying every %ss",
                        self._RECONNECT_DELAYS[-1],
                    )
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # Internal — task management
    # ------------------------------------------------------------------

    async def _cancel_tasks(self) -> None:
        for task in (self._listen_task, self._reconnect_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._listen_task = None
        self._reconnect_task = None

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<HAWebSocketClient url={self._ws_url!r} "
            f"connected={self.connected} "
            f"entities_seen={len(self._entities_seen)}>"
        )


__all__ = [
    "HAStateChange",
    "HAWebSocketAuthError",
    "HAWebSocketClient",
    "HAWebSocketError",
    "StateChangeCallback",
]
