"""Robust async MQTT client for ClimateIQ sensors."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import aiomqtt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SensorReading:
    """Normalized reading from any sensor device."""

    device_name: str
    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    occupancy: bool | None = None
    illuminance: float | None = None
    co2: float | None = None
    pm25: float | None = None
    voc: float | None = None
    battery: int | None = None
    link_quality: int | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


Callback = Callable[[SensorReading], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# Configurable JSON-path mapping for generic / AliExpress sensors
# ---------------------------------------------------------------------------
# Each entry maps a SensorReading field to a list of candidate JSON keys
# that cheap Zigbee sensors might use.  The first match wins.

DEFAULT_FIELD_PATHS: dict[str, list[str]] = {
    "temperature": [
        "temperature",
        "device_temperature",
        "local_temperature",
        "current_temperature",
        "temp",
    ],
    "humidity": [
        "humidity",
        "relative_humidity",
        "local_humidity",
        "humi",
    ],
    "pressure": [
        "pressure",
        "atmospheric_pressure",
        "air_pressure",
    ],
    "occupancy": [
        "occupancy",
        "presence",
        "motion",
        "pir",
        "human_presence",
        "presence_state",
    ],
    "illuminance": [
        "illuminance",
        "illuminance_lux",
        "brightness",
        "light_level",
        "lux",
    ],
    "co2": [
        "co2",
        "carbon_dioxide",
        "eco2",
    ],
    "pm25": [
        "pm25",
        "pm2_5",
        "pm2.5",
    ],
    "voc": [
        "voc",
        "voc_index",
        "volatile_organic_compounds",
    ],
    "battery": [
        "battery",
        "battery_percentage",
        "battery_level",
    ],
    "link_quality": [
        "linkquality",
        "link_quality",
        "lqi",
    ],
}


# ---------------------------------------------------------------------------
# MQTT Client
# ---------------------------------------------------------------------------


class MQTTClient:
    """aiomqtt-based client with automatic reconnection, device discovery,
    configurable payload parsing, and callback dispatch."""

    _RECONNECT_DELAYS = (1, 2, 5, 10, 30, 60)

    def __init__(
        self,
        *,
        broker: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        topic_prefix: str = "zigbee2mqtt",
        keepalive: int = 60,
        field_paths: dict[str, list[str]] | None = None,
    ) -> None:
        # Connection parameters
        self._broker = broker
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._keepalive = keepalive
        self._topic_prefix = topic_prefix.strip("/") or "zigbee2mqtt"

        # Configurable JSON-path mapping (for AliExpress / generic sensors)
        self._field_paths: dict[str, list[str]] = field_paths or dict(DEFAULT_FIELD_PATHS)

        # Internal state
        self._client_cm: aiomqtt.Client | None = None
        self._client: aiomqtt.Client | None = None
        self._connected = asyncio.Event()
        self._stop = False
        self._lock = asyncio.Lock()
        self._message_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None

        # Callbacks
        self._callbacks: list[Callback] = []

        # Subscriptions to restore after reconnect
        self._subscriptions: set[str] = set()

        # Pending one-shot topic listeners (for discover_devices, etc.)
        self._pending_topics: dict[str, list[asyncio.Future[Any]]] = {}

        # Device cache
        self._device_cache: dict[str, dict[str, Any]] | None = None
        self._device_cache_time: float = 0.0
        self._device_cache_ttl: float = 300.0  # 5 minutes

    # ------------------------------------------------------------------
    # Public API — lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the MQTT broker with exponential-backoff retry.

        On first call the client connects immediately.  If the broker is
        unreachable the method retries with delays from ``_RECONNECT_DELAYS``
        before raising the last exception.
        """
        async with self._lock:
            if self._client:
                return

            for attempt, delay in enumerate(self._RECONNECT_DELAYS):
                try:
                    await self._open_connection()
                    self._stop = False
                    self._message_task = asyncio.create_task(
                        self._message_loop(), name="mqtt-message-loop"
                    )
                    self._reconnect_task = asyncio.create_task(
                        self._reconnect_loop(), name="mqtt-reconnect-loop"
                    )
                    logger.info(
                        "MQTT connected to %s:%s (attempt %d)",
                        self._broker,
                        self._port,
                        attempt + 1,
                    )
                    return
                except Exception as exc:
                    logger.warning(
                        "MQTT connect attempt %d failed (%s), retrying in %ss",
                        attempt + 1,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

            # Final attempt — let it raise
            try:
                await self._open_connection()
                self._stop = False
                self._message_task = asyncio.create_task(
                    self._message_loop(), name="mqtt-message-loop"
                )
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_loop(), name="mqtt-reconnect-loop"
                )
                logger.info("MQTT connected to %s:%s (final attempt)", self._broker, self._port)
            except Exception as exc:
                logger.error("MQTT connect failed after all retries: %s", exc)
                raise

    async def disconnect(self) -> None:
        """Cleanly disconnect from the broker and cancel background tasks."""
        async with self._lock:
            self._stop = True
            self._connected.clear()
            await self._shutdown_tasks()
            if self._client_cm:
                with suppress(Exception):
                    await self._client_cm.__aexit__(None, None, None)
            self._client_cm = None
            self._client = None
            logger.info("MQTT disconnected from %s:%s", self._broker, self._port)

    # ------------------------------------------------------------------
    # Public API — subscribe / unsubscribe
    # ------------------------------------------------------------------

    async def subscribe(self, topics: str | list[str]) -> None:
        """Subscribe to one or more MQTT topics.

        Topics are automatically prefixed with the configured topic_prefix
        unless they already start with it.
        """
        await self._ensure_connected()
        if self._client is None:
            raise RuntimeError("MQTT client not connected")

        if isinstance(topics, str):
            topics = [topics]

        for topic in topics:
            full_topic = self._qualify(topic)
            await self._client.subscribe(full_topic)
            self._subscriptions.add(full_topic)
            logger.debug("Subscribed to %s", full_topic)

    async def unsubscribe(self, topics: str | list[str]) -> None:
        """Unsubscribe from one or more MQTT topics."""
        await self._ensure_connected()
        if self._client is None:
            raise RuntimeError("MQTT client not connected")

        if isinstance(topics, str):
            topics = [topics]

        for topic in topics:
            full_topic = self._qualify(topic)
            with suppress(Exception):
                await self._client.unsubscribe(full_topic)
            self._subscriptions.discard(full_topic)
            logger.debug("Unsubscribed from %s", full_topic)

    # ------------------------------------------------------------------
    # Public API — publish
    # ------------------------------------------------------------------

    async def publish(self, topic: str, payload: dict[str, Any] | str | bytes) -> None:
        """Publish a message.  Dicts are JSON-serialized automatically."""
        await self._ensure_connected()
        if self._client is None:
            raise RuntimeError("MQTT client not connected")

        if isinstance(payload, bytes):
            data = payload
        elif isinstance(payload, str):
            data = payload.encode()
        else:
            data = json.dumps(payload, separators=(",", ":")).encode()

        await self._client.publish(self._qualify(topic), payload=data)
        logger.debug("Published to %s (%d bytes)", self._qualify(topic), len(data))

    # ------------------------------------------------------------------
    # Public API — device discovery
    # ------------------------------------------------------------------

    async def discover_devices(
        self,
        *,
        force_refresh: bool = False,
        timeout_s: float = 15.0,
    ) -> dict[str, dict[str, Any]]:
        """Request the device list from zigbee2mqtt/bridge/devices.

        Returns a dict keyed by friendly_name with device info dicts as
        values.  Results are cached for ``_device_cache_ttl`` seconds
        unless *force_refresh* is ``True``.
        """
        # Return cached result when valid
        if (
            not force_refresh
            and self._device_cache is not None
            and (time.time() - self._device_cache_time) < self._device_cache_ttl
        ):
            logger.debug("Returning cached device list (%d devices)", len(self._device_cache))
            return self._device_cache

        await self._ensure_connected()
        loop = asyncio.get_running_loop()

        response_topic = f"{self._topic_prefix}/bridge/devices"
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_topics.setdefault(response_topic, []).append(future)

        # Ask zigbee2mqtt for the device list
        await self.publish(
            "bridge/request/devices",
            {"type": "devices", "response": "device_state"},
        )

        try:
            async with asyncio.timeout(timeout_s):
                result = await future
        except TimeoutError as exc:
            logger.warning("Device discovery timed out after %.1fs", timeout_s)
            raise RuntimeError(
                f"Device discovery timed out after {timeout_s}s — "
                "is zigbee2mqtt running and connected?"
            ) from exc
        finally:
            with suppress(ValueError):
                self._pending_topics.get(response_topic, []).remove(future)

        # zigbee2mqtt returns a JSON array of device objects
        if not isinstance(result, list):
            raise RuntimeError("Unexpected response from MQTT device discovery")

        # Build a dict keyed by friendly_name for easy lookup
        devices: dict[str, dict[str, Any]] = {}
        for device in result:
            if not isinstance(device, dict):
                continue
            friendly_name = device.get("friendly_name") or device.get("ieee_address", "unknown")
            devices[friendly_name] = device

        # Update cache
        self._device_cache = devices
        self._device_cache_time = time.time()

        logger.info("Discovered %d devices via MQTT", len(devices))
        return devices

    # ------------------------------------------------------------------
    # Public API — callbacks
    # ------------------------------------------------------------------

    def add_callback(self, callback: Callback) -> None:
        """Register a callback invoked for every parsed sensor reading."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def remove_callback(self, callback: Callback) -> None:
        """Unregister a previously registered callback."""
        with suppress(ValueError):
            self._callbacks.remove(callback)

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    def _handle_message(self, topic: str, payload: bytes) -> None:
        """Process an incoming MQTT message (called from the message loop).

        1. Decode the raw bytes to a Python object.
        2. Resolve any pending one-shot futures (e.g. discover_devices).
        3. Parse sensor values and dispatch to registered callbacks.
        """
        decoded = self._decode(payload)

        # Resolve pending futures (device discovery, etc.)
        self._resolve(topic, decoded)

        # Only attempt sensor parsing for dict payloads
        if isinstance(decoded, dict):
            reading = self._parse_payload_from_topic(topic, decoded)
            if reading is not None:
                self._dispatch(reading)

    # ------------------------------------------------------------------
    # Payload parsing (configurable JSON paths)
    # ------------------------------------------------------------------

    def _parse_payload(self, device_name: str, payload: dict[str, Any]) -> SensorReading | None:
        """Extract sensor values from *payload* using configurable JSON paths.

        This supports generic / AliExpress Zigbee sensors that may use
        non-standard key names.  The mapping is controlled by
        ``self._field_paths`` (see ``DEFAULT_FIELD_PATHS``).

        Returns a ``SensorReading`` if at least one sensor value was found,
        otherwise ``None``.
        """
        values: dict[str, Any] = {}

        for field_name, candidate_keys in self._field_paths.items():
            for key in candidate_keys:
                # Support dotted paths for nested payloads (e.g. "sensors.temperature")
                value = self._resolve_path(payload, key)
                if value is not None:
                    values[field_name] = value
                    break

        if not values:
            return None

        reading = SensorReading(
            device_name=device_name,
            temperature=self._as_float(values.get("temperature")),
            humidity=self._as_float(values.get("humidity")),
            pressure=self._as_float(values.get("pressure")),
            occupancy=self._as_bool(values.get("occupancy")),
            illuminance=self._as_float(values.get("illuminance")),
            co2=self._as_float(values.get("co2")),
            pm25=self._as_float(values.get("pm25")),
            voc=self._as_float(values.get("voc")),
            battery=self._as_int(values.get("battery")),
            link_quality=self._as_int(values.get("link_quality")),
            raw_payload=payload,
        )

        # Only return if at least one meaningful sensor value is present
        has_value = any(
            getattr(reading, f) is not None
            for f in (
                "temperature",
                "humidity",
                "pressure",
                "occupancy",
                "illuminance",
                "co2",
                "pm25",
                "voc",
                "battery",
            )
        )
        return reading if has_value else None

    def _parse_payload_from_topic(
        self, topic: str, payload: dict[str, Any]
    ) -> SensorReading | None:
        """Derive the device name from the MQTT topic and delegate to
        ``_parse_payload``."""
        if not topic.startswith(self._topic_prefix):
            return None

        # Topic format: <prefix>/<device_name>[/optional_suffix]
        # Skip bridge topics (bridge/state, bridge/devices, etc.)
        remainder = topic[len(self._topic_prefix) :].strip("/")
        if not remainder or remainder.startswith("bridge"):
            return None

        # The device name is the first path segment after the prefix
        device_name = remainder.split("/")[0]
        return self._parse_payload(device_name, payload)

    @staticmethod
    def _resolve_path(data: dict[str, Any], path: str) -> Any:
        """Resolve a dotted path like ``"sensors.temperature"`` in *data*.

        Returns ``None`` if any segment is missing.
        """
        current: Any = data
        for segment in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
            if current is None:
                return None
        return current

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _message_loop(self) -> None:
        """Subscribe to the wildcard topic and dispatch incoming messages."""
        if self._client is None:
            raise RuntimeError("MQTT client not connected")
        client = self._client
        try:
            # Subscribe to all topics under the prefix
            wildcard = f"{self._topic_prefix}/#"
            await client.subscribe(wildcard)
            logger.debug("Listening on %s", wildcard)

            async for message in client.messages:
                try:
                    self._handle_message(
                        message.topic.value,
                        message.payload if isinstance(message.payload, bytes) else b"",
                    )
                except Exception:
                    logger.exception("Error handling message on %s", message.topic.value)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MQTT message loop error — will trigger reconnect")
            self._connected.clear()
        finally:
            self._message_task = None

    async def _reconnect_loop(self) -> None:
        """Monitor the connection and automatically reconnect on failure.

        Uses exponential backoff with delays from ``_RECONNECT_DELAYS``.
        """
        try:
            while not self._stop:
                await asyncio.sleep(1)
                if self._connected.is_set():
                    continue

                logger.info("MQTT connection lost — starting reconnect sequence")
                for delay in self._RECONNECT_DELAYS:
                    if self._stop:
                        return
                    try:
                        await self._reopen()
                        logger.info("MQTT reconnected to %s:%s", self._broker, self._port)
                        break
                    except Exception as exc:
                        logger.warning("MQTT reconnect failed (%s), retrying in %ss", exc, delay)
                        await asyncio.sleep(delay)
                else:
                    # All delays exhausted — keep trying with the max delay
                    logger.error(
                        "MQTT reconnect exhausted backoff schedule; retrying every %ss",
                        self._RECONNECT_DELAYS[-1],
                    )
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # Internal helpers — connection management
    # ------------------------------------------------------------------

    async def _open_connection(self) -> None:
        """Create a new aiomqtt client and enter its context manager."""
        tls_ctx = ssl.create_default_context() if self._use_tls else None
        self._client_cm = aiomqtt.Client(
            hostname=self._broker,
            port=self._port,
            username=self._username,
            password=self._password,
            keepalive=self._keepalive,
            tls_context=tls_ctx,
        )
        if self._client_cm is None:
            raise RuntimeError("MQTT client context not initialized")
        self._client = await self._client_cm.__aenter__()
        self._connected.set()

    async def _reopen(self) -> None:
        """Tear down the old connection and establish a fresh one,
        restoring all active subscriptions."""
        async with self._lock:
            # Tear down old connection
            if self._client_cm:
                with suppress(Exception):
                    await self._client_cm.__aexit__(None, None, None)

            await self._open_connection()

            # Restore subscriptions
            if self._client is None:
                raise RuntimeError("MQTT client not connected")
            for topic in self._subscriptions:
                with suppress(Exception):
                    await self._client.subscribe(topic)

            # Restart message loop if it died
            if self._message_task is None or self._message_task.done():
                self._message_task = asyncio.create_task(
                    self._message_loop(), name="mqtt-message-loop"
                )

    async def _shutdown_tasks(self) -> None:
        """Cancel the message and reconnect background tasks."""
        await self._cancel(self._message_task)
        await self._cancel(self._reconnect_task)
        self._message_task = None
        self._reconnect_task = None

    @staticmethod
    async def _cancel(task: asyncio.Task[None] | None) -> None:
        if not task or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _ensure_connected(self) -> None:
        """Lazily connect if not already connected, then wait for the
        ``_connected`` event."""
        if not self._client:
            await self.connect()
        await self._connected.wait()

    # ------------------------------------------------------------------
    # Internal helpers — decoding & dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(payload: bytes) -> Any:
        """Decode raw MQTT bytes to a Python object (dict/list/str)."""
        if not payload:
            return {}
        try:
            return json.loads(payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _resolve(self, topic: str, payload: Any) -> None:
        """Resolve any pending futures waiting for a specific topic."""
        futures = self._pending_topics.get(topic)
        if not futures:
            return
        for future in list(futures):
            if not future.done():
                future.set_result(payload)

    def _dispatch(self, reading: SensorReading) -> None:
        """Invoke all registered callbacks with the given reading."""
        for callback in list(self._callbacks):
            try:
                result = callback(reading)
                if asyncio.iscoroutine(result):
                    task = asyncio.create_task(result)
                    task.add_done_callback(lambda t: t.exception())
            except Exception:
                logger.exception("MQTT callback raised an exception")

    def _qualify(self, topic: str) -> str:
        """Prefix *topic* with the configured topic_prefix if needed."""
        topic = topic.strip("/")
        if topic.startswith(self._topic_prefix):
            return topic
        return f"{self._topic_prefix}/{topic}"

    # ------------------------------------------------------------------
    # Internal helpers — type coercion
    # ------------------------------------------------------------------

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"true", "on", "1", "occupied", "detected", "yes"}:
                return True
            if lowered in {"false", "off", "0", "clear", "unoccupied", "no"}:
                return False
        return None


__all__ = ["DEFAULT_FIELD_PATHS", "MQTTClient", "SensorReading"]
