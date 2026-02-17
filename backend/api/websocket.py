"""WebSocket connection manager with Redis fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from fastapi import WebSocket

from backend.api.dependencies import get_redis

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ConnectionManager:
    """Track active WebSocket clients and bridge updates across instances."""

    _DEFAULT_CHANNEL = "climateiq:ws:broadcast"
    _SENSOR_CHANNEL = "climateiq:ws:sensors"
    _DEVICE_CHANNEL = "climateiq:ws:devices"

    def __init__(self, redis_url: str, redis_channel: str | None = None) -> None:
        self._redis_url = redis_url
        self._channel = redis_channel or self._DEFAULT_CHANNEL
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None

        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self, websocket: WebSocket, channel: str | None = None) -> None:
        """Accept a WebSocket connection and register it.

        This is the primary entry point used by route handlers.
        """
        ch = channel or "general"
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(ch, set()).add(websocket)
        logger.debug(
            "WebSocket connected to channel %s; %s clients active", ch, self.get_connection_count()
        )

    # Alias for internal / explicit naming
    connect_client = connect

    async def disconnect_client(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from all channels and close it."""
        removed = False
        async with self._lock:
            for ch_set in self._connections.values():
                if websocket in ch_set:
                    ch_set.discard(websocket)
                    removed = True
        if removed:
            await self._safe_close(websocket)

    def disconnect_websocket(self, websocket: WebSocket) -> None:
        """Compatibility helper for legacy call sites (fire-and-forget)."""
        task = asyncio.create_task(self.disconnect_client(websocket))
        task.add_done_callback(
            lambda t: (
                logger.debug("WS disconnect task error", exc_info=t.exception())
                if t.exception()
                else None
            )
        )

    async def disconnect(self, websocket: WebSocket, channel: str | None = None) -> None:
        """Remove a WebSocket from a specific channel (or all channels)."""
        if channel is not None:
            async with self._lock:
                ch_set = self._connections.get(channel)
                if ch_set and websocket in ch_set:
                    ch_set.discard(websocket)
            await self._safe_close(websocket)
        else:
            await self.disconnect_client(websocket)

    async def disconnect_all(self) -> None:
        async with self._lock:
            all_ws: list[WebSocket] = []
            for ch_set in self._connections.values():
                all_ws.extend(ch_set)
            self._connections.clear()
        for websocket in all_ws:
            await self._safe_close(websocket)

    async def shutdown(self) -> None:
        await self.disconnect_all()
        await self._disconnect_redis()

    async def connect_redis(self, redis_url: str | None = None) -> None:
        if redis_url:
            self._redis_url = redis_url
        await self.subscribe_redis()

    async def subscribe_redis(self) -> None:
        if self._listener_task and not self._listener_task.done():
            return

        redis_conn = await self._ensure_redis()
        if not redis_conn:
            return

        pubsub = redis_conn.pubsub()
        await pubsub.subscribe(self._channel, self._SENSOR_CHANNEL, self._DEVICE_CHANNEL)
        self._pubsub = pubsub

        async def _listen() -> None:
            if self._pubsub is None:
                return
            try:
                async for message in self._pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    raw = message.get("data")
                    if not isinstance(raw, str):
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed Redis payload: %s", raw)
                        continue
                    await self._send_local(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Redis listener crashed; retrying in 2s")
                await asyncio.sleep(2)
                await self.subscribe_redis()
            finally:
                if self._pubsub:
                    with suppress(Exception):
                        await self._pubsub.close()
                self._pubsub = None

        self._listener_task = asyncio.create_task(_listen(), name="climateiq-ws-redis")

    async def shutdown_manager(self) -> None:
        await self.shutdown()

    async def cleanup_stale(self) -> int:
        """Remove WebSocket connections that are no longer responsive."""
        stale: list[WebSocket] = []
        async with self._lock:
            clients: list[WebSocket] = []
            for ch_set in self._connections.values():
                clients.extend(ch_set)

        for ws in clients:
            try:
                # Send a ping to check if connection is alive
                await ws.send_json({"type": "ping"})
            except Exception:
                stale.append(ws)

        for ws in stale:
            await self._safe_close(ws)
            async with self._lock:
                for ch_set in self._connections.values():
                    ch_set.discard(ws)

        return len(stale)

    async def broadcast_all(self, message: dict[str, Any]) -> None:
        """Send a message to all connections across all channels."""
        await self._send_local(message)
        await self.publish_redis(message)

    def get_connection_count(self) -> int:
        return sum(len(ch_set) for ch_set in self._connections.values())

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------
    async def broadcast(self, message: dict[str, Any]) -> None:
        await self._send_local(message)
        await self.publish_redis(message)

    async def broadcast_sensor_update(
        self,
        sensor_id: str | None,
        reading: Mapping[str, Any],
    ) -> None:
        payload = self._serialize_sensor_payload(sensor_id, reading)
        await self.broadcast(payload)

    async def broadcast_device_state(
        self,
        device_id: str | None,
        state: Mapping[str, Any],
    ) -> None:
        payload = {
            "type": "device_state",
            "device_id": str(device_id) if device_id is not None else None,
            "state": dict(state),
            "timestamp": _utcnow().isoformat(),
        }
        await self._send_local(payload)
        await self.publish_redis(payload, channel=self._DEVICE_CHANNEL)

    async def publish_redis(
        self,
        message: dict[str, Any],
        *,
        channel: str | None = None,
    ) -> None:
        redis_conn = await self._ensure_redis()
        if not redis_conn:
            return
        target = channel or self._channel
        payload = json.dumps(message, default=str)
        try:
            await redis_conn.publish(target, payload)
        except Exception:
            logger.exception("Failed to publish WebSocket payload to Redis")

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------
    async def _ensure_redis(self) -> redis.Redis | None:
        if self._redis:
            return self._redis
        try:
            async for client in get_redis():
                self._redis = client
                break
        except Exception:
            logger.exception("Redis connection failed; WS fan-out disabled")
            self._redis = None
        return self._redis

    async def _disconnect_redis(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener_task
        self._listener_task = None

        if self._pubsub:
            with suppress(Exception):
                await self._pubsub.close()
        self._pubsub = None

        if self._redis:
            with suppress(Exception):
                await self._redis.close()
        self._redis = None

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------
    async def _send_local(self, message: dict[str, Any], *, channel: str | None = None) -> None:
        async with self._lock:
            if channel is not None:
                clients = list(self._connections.get(channel, set()))
            else:
                # Send to all channels
                clients = []
                for ch_set in self._connections.values():
                    clients.extend(ch_set)
        if not clients:
            return
        payload = json.dumps(message, default=str)
        disconnected: list[WebSocket] = []
        for websocket in clients:
            try:
                await websocket.send_text(payload)
            except Exception:
                logger.debug("WebSocket send failed; scheduling removal", exc_info=True)
                disconnected.append(websocket)
        for websocket in disconnected:
            await self._safe_close(websocket)
            async with self._lock:
                for ch_set in self._connections.values():
                    ch_set.discard(websocket)

    def _serialize_sensor_payload(
        self,
        sensor_id: str | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "type": "sensor_update",
            "sensor_id": sensor_id,
            "timestamp": _utcnow().isoformat(),
            "data": dict(payload),
        }

    async def _safe_close(self, websocket: WebSocket) -> None:
        with suppress(Exception):
            await websocket.close()


__all__ = [
    "ConnectionManager",
]
