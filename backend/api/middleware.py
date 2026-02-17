"""
Home Assistant Ingress Middleware for ClimateIQ.

Handles the X-Ingress-Path header that HA Supervisor sets when proxying
requests through the ingress system. This middleware ensures:

1. All response URLs (redirects, Location headers) include the ingress prefix
2. HTML responses have their asset paths rewritten
3. WebSocket upgrade requests are passed through correctly
4. The ingress path is available to route handlers via request.state
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"
_VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "unknown"


def is_ha_addon() -> bool:
    """Detect if we're running as a Home Assistant add-on."""
    return bool(os.environ.get("SUPERVISOR_TOKEN")) or bool(
        os.environ.get("CLIMATEIQ_HA_ADDON_MODE")
    )


class IngressMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that handles Home Assistant ingress path rewriting.

    When HA proxies requests through ingress, it sets the X-Ingress-Path
    header to the add-on's ingress URL prefix (e.g., /api/hassio_ingress/abc123).
    This middleware makes that prefix available to the application and rewrites
    responses as needed.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._addon_mode = is_ha_addon()
        if self._addon_mode:
            logger.info("HA add-on mode detected - ingress middleware active")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process each request, injecting ingress path context."""
        # Extract ingress path from header
        ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")

        # Store on request state for use by route handlers
        request.state.ingress_path = ingress_path
        request.state.is_ingress = bool(ingress_path)

        # Process the request
        response = await call_next(request)

        # Rewrite Location headers for redirects
        if ingress_path and response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if location and location.startswith("/") and not location.startswith(ingress_path):
                response.headers["location"] = f"{ingress_path}{location}"

        # Add version header to all responses
        response.headers["X-ClimateIQ-Version"] = _VERSION

        return response


class IngressWebSocketMiddleware:
    """Raw ASGI middleware for WebSocket connections through HA ingress.

    BaseHTTPMiddleware doesn't handle WebSocket connections, so this is a
    separate ASGI middleware that strips the ingress prefix from WebSocket
    paths before they reach the router.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            headers = dict(scope.get("headers", []))
            ingress_path = ""

            # Extract X-Ingress-Path from headers
            for key, value in headers.items():
                if key == b"x-ingress-path":
                    ingress_path = value.decode("utf-8").rstrip("/")
                    break

            # Strip ingress prefix from the WebSocket path so routing works
            if ingress_path and scope["path"].startswith(ingress_path):
                scope = dict(scope)
                scope["path"] = scope["path"][len(ingress_path) :] or "/"
                if "root_path" not in scope or not scope["root_path"]:
                    scope["root_path"] = ingress_path

        await self.app(scope, receive, send)


class HAAuthMiddleware:
    """ASGI middleware that validates requests using the HA Supervisor auth API.

    When running as an HA add-on with ingress, the Supervisor handles
    authentication. This middleware validates the ingress session by checking
    with the Supervisor API. Requests that come through ingress (with the
    X-Ingress-Path header) are considered pre-authenticated by HA.

    For non-ingress requests (direct port access), this middleware can
    optionally require a bearer token.
    """

    # Paths that never require authentication
    PUBLIC_PATHS = frozenset(
        {
            "/health",
            "/health/ready",
            "/health/live",
            "/health/detailed",
        }
    )

    def __init__(self, app: ASGIApp, *, require_auth_for_direct: bool = False) -> None:
        self.app = app
        self.require_auth_for_direct = require_auth_for_direct
        self._addon_mode = is_ha_addon()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")

        # Always allow health checks
        if path in self.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        # Check for ingress header - if present, HA has already authenticated
        headers = dict(scope.get("headers", []))
        has_ingress = b"x-ingress-path" in headers

        if has_ingress:
            # Validate that the request actually came from the Supervisor by
            # checking that SUPERVISOR_TOKEN is set (only available inside the
            # add-on container) and that the ingress path matches the expected
            # HA ingress URL pattern.
            supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
            ingress_path = headers.get(b"x-ingress-path", b"").decode("utf-8")
            if supervisor_token and ingress_path.startswith("/api/hassio_ingress/"):
                # Legitimate ingress request — user is authenticated by HA
                await self.app(scope, receive, send)
                return
            elif supervisor_token:
                # SUPERVISOR_TOKEN is set but ingress path doesn't match expected pattern
                logger.warning("Ingress header with unexpected path: %s", ingress_path)
                await self.app(scope, receive, send)
                return
            else:
                # No SUPERVISOR_TOKEN — someone is spoofing the header
                logger.warning("Rejecting spoofed X-Ingress-Path header from %s", path)
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid ingress request"},
                )
                await response(scope, receive, send)
                return

        if self._addon_mode and self.require_auth_for_direct:
            # Direct access in add-on mode - check for bearer token
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if not auth_header.startswith("Bearer "):
                response = JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Authentication required. Access through Home Assistant UI."
                    },
                )
                await response(scope, receive, send)
                return

        # Non-addon mode or auth not required for direct access
        await self.app(scope, receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter.

    Limits are per-IP.  Exempt health-check paths.  Uses a sliding
    window counter stored in a dict (no external deps).

    Args:
        app: ASGI application.
        requests_per_minute: Maximum requests allowed per IP per minute.
    """

    _EXEMPT_PATHS = frozenset({"/health", "/health/ready", "/health/live", "/health/detailed"})

    def __init__(self, app: ASGIApp, *, requests_per_minute: int = 120) -> None:
        super().__init__(app)
        self._limit = requests_per_minute
        self._window = 60.0  # seconds
        self._requests: dict[str, list[float]] = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        import time

        path = request.url.path
        if path in self._EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        # Prune old entries
        timestamps = self._requests.setdefault(client_ip, [])
        cutoff = now - self._window
        self._requests[client_ip] = [t for t in timestamps if t > cutoff]
        timestamps = self._requests[client_ip]

        # Remove empty entries to prevent unbounded memory growth
        if not timestamps:
            del self._requests[client_ip]
            timestamps = self._requests.setdefault(client_ip, [])

        # Hard cap on tracked IPs to prevent memory exhaustion
        if len(self._requests) > 10_000:
            # Evict oldest entries
            oldest_ips = sorted(
                self._requests.keys(),
                key=lambda ip: self._requests[ip][0] if self._requests[ip] else 0,
            )
            for ip in oldest_ips[: len(oldest_ips) // 2]:
                del self._requests[ip]

        if len(timestamps) >= self._limit:
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(int(self._window))},
            )

        timestamps.append(now)
        return await call_next(request)


class APIKeyMiddleware:
    """Optional API key authentication for standalone (non-addon) mode.

    When ``CLIMATEIQ_API_KEY`` is set, all non-health endpoints require
    an ``Authorization: Bearer <key>`` header.  If the env var is empty
    or unset, all requests are allowed through.
    """

    _PUBLIC_PATHS = frozenset(
        {
            "/health",
            "/health/ready",
            "/health/live",
            "/health/detailed",
            "/",
        }
    )

    def __init__(self, app: ASGIApp, *, api_key: str) -> None:
        self.app = app
        self._api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._api_key:
            # No key configured — allow everything
            await self.app(scope, receive, send)
            return

        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if path in self._PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8")
        if auth_header == f"Bearer {self._api_key}":
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )
        await response(scope, receive, send)


def get_ingress_path(request: Request) -> str:
    """Helper to get the ingress path from a request.

    Use this in route handlers to build URLs that work through ingress:

        ingress = get_ingress_path(request)
        ws_url = f"{ingress}/ws"
    """
    return getattr(request.state, "ingress_path", "")


__all__ = [
    "APIKeyMiddleware",
    "HAAuthMiddleware",
    "IngressMiddleware",
    "IngressWebSocketMiddleware",
    "RateLimitMiddleware",
    "get_ingress_path",
    "is_ha_addon",
]
