"""Home Assistant REST API client for ClimateIQ.

Provides a fully async wrapper around the Home Assistant REST API with
typed entity states, structured error handling, and convenience helpers
for climate / cover / switch / fan domains.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HAClientError(Exception):
    """Base exception for all HA client errors."""


class HAConnectionError(HAClientError):
    """Raised when the client cannot reach Home Assistant."""


class HAAuthenticationError(HAClientError):
    """Raised on 401 Unauthorized responses."""


class HANotFoundError(HAClientError):
    """Raised on 404 Not Found responses (bad entity / service)."""


class HAServiceError(HAClientError):
    """Raised when a service call fails (4xx / 5xx other than 401/404)."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EntityState:
    """Snapshot of a single Home Assistant entity."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str = ""
    last_updated: str = ""

    @property
    def domain(self) -> str:
        """Return the domain portion of the entity id (e.g. ``climate``)."""
        return self.entity_id.split(".", 1)[0]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityState:
        """Build an ``EntityState`` from a raw HA JSON dict."""
        return cls(
            entity_id=data.get("entity_id", ""),
            state=str(data.get("state", "")),
            attributes=data.get("attributes") or {},
            last_changed=data.get("last_changed", ""),
            last_updated=data.get("last_updated", ""),
        )


class HVACMode(StrEnum):
    """Standard HVAC modes supported by Home Assistant climate entities."""

    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"
    AUTO = "auto"
    DRY = "dry"
    FAN_ONLY = "fan_only"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class HAClient:
    """Async REST wrapper for the Home Assistant API.

    Usage::

        client = HAClient("http://homeassistant.local:8123", token="ey...")
        await client.connect()
        state = await client.get_state("climate.living_room")
        await client.set_temperature("climate.living_room", 22.0)
        await client.disconnect()

    The client can also be used as an async context manager::

        async with HAClient(url, token=token) as client:
            await client.get_states()
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 15.0,
        verify_ssl: bool = True,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._client: httpx.AsyncClient | None = None
        self._connected: bool = False

    # -- async context manager ------------------------------------------------

    async def __aenter__(self) -> HAClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # -- lifecycle ------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return ``True`` when the client has an active, verified session."""
        return self._connected

    async def connect(self) -> None:
        """Initialise the ``httpx.AsyncClient`` and verify the connection.

        Raises:
            RuntimeError: If no token was provided.
            HAAuthenticationError: If the token is rejected (401).
            HAConnectionError: If the server is unreachable.
        """
        if self._connected:
            logger.debug("HAClient already connected to %s", self._base_url)
            return

        if not self._token:
            raise RuntimeError("Home Assistant long-lived access token is required")

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
            verify=self._verify_ssl,
        )

        logger.info("Connecting to Home Assistant at %s ...", self._base_url)

        assert self._client is not None  # noqa: S101 - just assigned above
        try:
            response = await self._client.get("/api/")
            self._raise_for_status(response, context="connect")
            logger.info(
                "Connected to Home Assistant - API message: %s",
                response.json().get("message", "ok"),
            )
        except (HAClientError, RuntimeError):
            await self.disconnect()
            raise
        except httpx.ConnectError as exc:
            await self.disconnect()
            msg = f"Cannot reach Home Assistant at {self._base_url}: {exc}"
            logger.error(msg)
            raise HAConnectionError(msg) from exc
        except httpx.TimeoutException as exc:
            await self.disconnect()
            msg = f"Connection to Home Assistant timed out ({self._timeout}s)"
            logger.error(msg)
            raise HAConnectionError(msg) from exc
        except Exception as exc:
            await self.disconnect()
            logger.error("Unexpected error during HA connect: %s", exc)
            raise HAConnectionError(str(exc)) from exc

        self._connected = True

    async def disconnect(self) -> None:
        """Close the underlying HTTP client and reset state."""
        self._connected = False
        if self._client is not None:
            with suppress(Exception):
                await self._client.aclose()
            self._client = None
            logger.info("Disconnected from Home Assistant")

    # -- internal request helper ----------------------------------------------

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        context: str = "",
    ) -> None:
        """Translate HTTP error codes into typed exceptions."""
        if response.is_success:
            return

        status = response.status_code
        detail = response.text[:300]
        prefix = f"[{context}] " if context else ""

        if status == 401:
            msg = f"{prefix}Authentication failed (401). Check your HA token."
            logger.error(msg)
            raise HAAuthenticationError(msg)
        if status == 404:
            msg = f"{prefix}Resource not found (404): {detail}"
            logger.warning(msg)
            raise HANotFoundError(msg)
        if 400 <= status < 500:
            msg = f"{prefix}Client error {status}: {detail}"
            logger.error(msg)
            raise HAServiceError(msg)
        # 5xx
        msg = f"{prefix}Server error {status}: {detail}"
        logger.error(msg)
        raise HAServiceError(msg)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        context: str = "",
    ) -> httpx.Response:
        """Send an HTTP request, auto-connecting if needed.

        Raises:
            HAConnectionError: On network-level failures.
            HAAuthenticationError / HANotFoundError / HAServiceError: On HTTP errors.
        """
        if self._client is None:
            await self.connect()
        assert self._client is not None  # noqa: S101 - guaranteed by connect()

        logger.debug("%s %s (json=%s)", method, path, json is not None)

        try:
            response = await self._client.request(method, path, json=json)
        except httpx.ConnectError as exc:
            self._connected = False
            msg = f"Lost connection to Home Assistant: {exc}"
            logger.error(msg)
            raise HAConnectionError(msg) from exc
        except httpx.TimeoutException as exc:
            msg = f"Request to {path} timed out"
            logger.error(msg)
            raise HAConnectionError(msg) from exc

        self._raise_for_status(response, context=context or f"{method} {path}")
        return response

    # -- core API methods -----------------------------------------------------

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Home Assistant service.

        Args:
            domain: Service domain (e.g. ``climate``, ``switch``).
            service: Service name (e.g. ``turn_on``, ``set_temperature``).
            data: Optional service data payload.
            target: Optional target dict (``entity_id``, ``area_id``, etc.).

        Returns:
            Parsed JSON response (usually a list of changed states) or ``None``.
        """
        payload: dict[str, Any] = dict(data or {})
        if target:
            payload["target"] = target

        path = f"/api/services/{domain}/{service}"
        logger.info("Calling service %s.%s → %s", domain, service, target or "no target")

        response = await self._request(
            "POST", path, json=payload, context=f"service:{domain}.{service}"
        )

        if response.headers.get("content-type", "").startswith("application/json"):
            result = response.json()
            logger.debug(
                "Service %s.%s returned %d state(s)",
                domain,
                service,
                len(result) if isinstance(result, list) else 0,
            )
            return result
        return None

    async def get_state(self, entity_id: str) -> EntityState:
        """Fetch the current state of a single entity.

        Raises:
            HANotFoundError: If the entity does not exist.
        """
        logger.debug("Fetching state for %s", entity_id)
        response = await self._request(
            "GET", f"/api/states/{entity_id}", context=f"get_state({entity_id})"
        )
        state = EntityState.from_dict(response.json())
        logger.debug("State %s = %s", entity_id, state.state)
        return state

    async def get_states(self) -> list[EntityState]:
        """Fetch the state of every entity known to Home Assistant."""
        logger.debug("Fetching all entity states")
        response = await self._request("GET", "/api/states", context="get_states")
        states = [EntityState.from_dict(item) for item in response.json()]
        logger.info("Retrieved %d entity states", len(states))
        return states

    async def get_config(self) -> dict[str, Any]:
        """Return the Home Assistant server configuration.

        Includes location, unit system, version, components, etc.
        """
        logger.debug("Fetching HA configuration")
        response = await self._request("GET", "/api/config", context="get_config")
        config: dict[str, Any] = response.json()
        logger.info(
            "HA config: version=%s, location=%s",
            config.get("version", "?"),
            config.get("location_name", "?"),
        )
        return config

    async def get_device_registry(self) -> list[dict[str, Any]]:
        """Fetch the HA device registry.

        Returns a list of device dicts with keys like ``id``, ``name``,
        ``manufacturer``, ``model``, ``area_id``, ``identifiers``, etc.
        Requires HA 2023.3+.
        """
        logger.debug("Fetching HA device registry")
        response = await self._request(
            "GET", "/api/config/device_registry", context="get_device_registry"
        )
        devices: list[dict[str, Any]] = response.json()
        logger.info("Retrieved %d devices from HA registry", len(devices))
        return devices

    async def get_entity_registry(self) -> list[dict[str, Any]]:
        """Fetch the HA entity registry.

        Returns a list of entity dicts with keys like ``entity_id``,
        ``device_id``, ``platform``, ``original_name``, ``unique_id``, etc.
        Requires HA 2023.3+.
        """
        logger.debug("Fetching HA entity registry")
        response = await self._request(
            "GET", "/api/config/entity_registry", context="get_entity_registry"
        )
        entities: list[dict[str, Any]] = response.json()
        logger.info("Retrieved %d entities from HA registry", len(entities))
        return entities

    # -- climate helpers ------------------------------------------------------

    async def set_temperature(self, entity_id: str, temperature: float) -> Any:
        """Set the target temperature on a climate entity.

        Args:
            entity_id: Fully qualified entity id (e.g. ``climate.living_room``).
            temperature: Desired target temperature.
        """
        logger.info("Setting temperature on %s to %.1f", entity_id, temperature)
        return await self.call_service(
            "climate",
            "set_temperature",
            data={"temperature": temperature},
            target={"entity_id": entity_id},
        )

    # Alias used by DecisionEngine (``set_climate_temperature``).
    set_climate_temperature = set_temperature

    async def set_hvac_mode(self, entity_id: str, mode: str) -> Any:
        """Set the HVAC mode on a climate entity.

        Args:
            entity_id: Climate entity id.
            mode: One of ``off``, ``heat``, ``cool``, ``heat_cool``,
                  ``auto``, ``dry``, ``fan_only``.
        """
        logger.info("Setting HVAC mode on %s to %s", entity_id, mode)
        return await self.call_service(
            "climate",
            "set_hvac_mode",
            data={"hvac_mode": mode},
            target={"entity_id": entity_id},
        )

    # -- cover helpers --------------------------------------------------------

    async def set_cover_position(self, entity_id: str, position: int) -> Any:
        """Set the position of a cover entity (0 = closed, 100 = open).

        Args:
            entity_id: Cover entity id.
            position: Integer 0-100.
        """
        if not 0 <= position <= 100:
            raise ValueError(f"Cover position must be 0-100, got {position}")
        logger.info("Setting cover %s position to %d%%", entity_id, position)
        return await self.call_service(
            "cover",
            "set_cover_position",
            data={"position": position},
            target={"entity_id": entity_id},
        )

    # -- switch / fan / generic helpers ---------------------------------------

    async def turn_on(self, entity_id: str) -> Any:
        """Turn on a switch, fan, light, or other toggleable entity."""
        domain = entity_id.split(".", 1)[0]
        logger.info("Turning on %s (domain=%s)", entity_id, domain)
        return await self.call_service(
            domain,
            "turn_on",
            target={"entity_id": entity_id},
        )

    async def turn_off(self, entity_id: str) -> Any:
        """Turn off a switch, fan, light, or other toggleable entity."""
        domain = entity_id.split(".", 1)[0]
        logger.info("Turning off %s (domain=%s)", entity_id, domain)
        return await self.call_service(
            domain,
            "turn_off",
            target={"entity_id": entity_id},
        )

    async def set_fan_speed(self, entity_id: str, percentage: int) -> Any:
        """Set the speed of a fan entity as a percentage (0-100).

        Args:
            entity_id: Fan entity id.
            percentage: Integer 0-100 where 0 effectively turns the fan off.
        """
        if not 0 <= percentage <= 100:
            raise ValueError(f"Fan percentage must be 0-100, got {percentage}")
        logger.info("Setting fan %s speed to %d%%", entity_id, percentage)
        return await self.call_service(
            "fan",
            "set_percentage",
            data={"percentage": percentage},
            target={"entity_id": entity_id},
        )

    # -- dunder ---------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<HAClient url={self._base_url!r} connected={self._connected}>"


__all__ = [
    "EntityState",
    "HAAuthenticationError",
    "HAClient",
    "HAClientError",
    "HAConnectionError",
    "HANotFoundError",
    "HAServiceError",
    "HVACMode",
]
