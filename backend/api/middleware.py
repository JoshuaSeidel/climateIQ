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

import hmac
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
    """ASGI auth for add-on mode.

    The API listens on the host network, so it is reachable from the whole
    LAN.  Two kinds of request are allowed:

    * **Home Assistant ingress** — proxied by the Supervisor, which has
      already authenticated the HA user.  Identified by the ``X-Ingress-Path``
      header *and* the Supervisor's source address (``172.30.32.2``).  The
      header alone is not enough: any LAN client can send it (previously
      that bypassed auth entirely).
    * **Direct access with the API key** — ``Authorization: Bearer <key>``,
      or ``?api_key=<key>`` for WebSocket clients.  See
      ``backend.core.api_key``.

    Everything else gets 401.  Health checks are always public.
    """

    PUBLIC_PATHS = frozenset(
        {
            "/health",
            "/health/ready",
            "/health/live",
            "/health/detailed",
        }
    )
    # Supervisor ingress proxy address (HA docs).  Overridable for unusual
    # network setups via CLIMATEIQ_INGRESS_TRUSTED_IPS (comma-separated).
    DEFAULT_INGRESS_IPS = frozenset({"172.30.32.2"})

    def __init__(self, app: ASGIApp, *, require_auth_for_direct: bool = True) -> None:
        self.app = app
        self.require_auth_for_direct = require_auth_for_direct
        extra = os.environ.get("CLIMATEIQ_INGRESS_TRUSTED_IPS", "")
        self._ingress_ips = self.DEFAULT_INGRESS_IPS | {
            ip.strip() for ip in extra.split(",") if ip.strip()
        }
        self._logged_ingress_ip = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if path in self.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        client = scope.get("client") or ("", 0)
        client_ip = str(client[0] or "")

        # ── HA ingress ────────────────────────────────────────────────
        if b"x-ingress-path" in headers:
            if client_ip in self._ingress_ips and os.environ.get("SUPERVISOR_TOKEN"):
                if not self._logged_ingress_ip:
                    logger.info("HA ingress requests arriving from %s", client_ip)
                    self._logged_ingress_ip = True
                await self.app(scope, receive, send)
                return
            logger.warning(
                "Ingress header from untrusted address %s on %s — treating as a "
                "direct request (set CLIMATEIQ_INGRESS_TRUSTED_IPS if this is the "
                "Supervisor)",
                client_ip, path,
            )

        if not self.require_auth_for_direct:
            await self.app(scope, receive, send)
            return

        # ── Direct access: API key ────────────────────────────────────
        from backend.core.api_key import key_matches

        auth_header = headers.get(b"authorization", b"").decode("utf-8", "replace")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        if not token and scope["type"] == "websocket":
            from urllib.parse import parse_qs

            qs = parse_qs(scope.get("query_string", b"").decode("utf-8", "replace"))
            token = (qs.get("api_key") or [""])[0]
        if key_matches(token):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Reject the handshake.
            await send({"type": "websocket.close", "code": 4401})
            return
        response = JSONResponse(
            status_code=401,
            content={
                "detail": (
                    "Authentication required: open ClimateIQ from the Home Assistant "
                    "sidebar, or send 'Authorization: Bearer <API key>' "
                    "(Settings → API Access)."
                )
            },
        )
        await response(scope, receive, send)


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
        if hmac.compare_digest(auth_header, f"Bearer {self._api_key}"):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Browsers cannot set headers on a WebSocket: accept ?api_key=.
            from urllib.parse import parse_qs

            qs = parse_qs(scope.get("query_string", b"").decode("utf-8", "replace"))
            if hmac.compare_digest(qs.get("api_key", [""])[0], self._api_key):
                await self.app(scope, receive, send)
                return
            await send({"type": "websocket.close", "code": 4001})
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
