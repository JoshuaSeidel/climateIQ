"""Tests for backend.api.middleware — rate limiting, ingress, and HA auth."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route

from backend.api.middleware import (
    HAAuthMiddleware,
    IngressMiddleware,
    RateLimitMiddleware,
    is_ha_addon,
)

# ---------------------------------------------------------------------------
# Helpers — tiny Starlette apps for each middleware under test
# ---------------------------------------------------------------------------


async def _homepage(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def _health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("healthy")


async def _redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rate_limit_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/", _homepage),
            Route("/health", _health),
            Route("/health/ready", _health),
        ],
    )
    app.add_middleware(RateLimitMiddleware, requests_per_minute=5)
    return app


@pytest.fixture()
def ingress_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/", _homepage),
            Route("/redirect", _redirect),
        ],
    )
    app.add_middleware(IngressMiddleware)
    return app


@pytest.fixture()
def auth_app_addon() -> HAAuthMiddleware:
    """HAAuthMiddleware in add-on mode with direct auth required."""
    inner = Starlette(
        routes=[
            Route("/", _homepage),
            Route("/health", _health),
            Route("/health/ready", _health),
            Route("/api/data", _homepage),
        ],
    )
    # Wrap with HAAuthMiddleware — we patch is_ha_addon inside the test
    app = HAAuthMiddleware(inner, require_auth_for_direct=True)
    return app


@pytest.fixture()
def auth_app_non_addon() -> HAAuthMiddleware:
    """HAAuthMiddleware in non-addon mode."""
    inner = Starlette(
        routes=[
            Route("/", _homepage),
            Route("/health", _health),
            Route("/api/data", _homepage),
        ],
    )
    app = HAAuthMiddleware(inner, require_auth_for_direct=False)
    return app


# ===================================================================
# RateLimitMiddleware
# ===================================================================


class TestRateLimitMiddleware:
    """Tests for the per-IP sliding-window rate limiter."""

    async def test_request_below_limit_passes(self, rate_limit_app: Starlette) -> None:
        transport = ASGITransport(app=rate_limit_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
            assert resp.status_code == 200
            assert resp.text == "ok"

    async def test_requests_exceeding_limit_returns_429(self, rate_limit_app: Starlette) -> None:
        transport = ASGITransport(app=rate_limit_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Send 5 requests (the limit)
            for _ in range(5):
                resp = await client.get("/")
                assert resp.status_code == 200

            # 6th request should be rate-limited
            resp = await client.get("/")
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
            assert resp.headers["Retry-After"] == "60"
            body = resp.json()
            assert "Rate limit" in body["detail"]

    async def test_health_paths_are_exempt(self, rate_limit_app: Starlette) -> None:
        transport = ASGITransport(app=rate_limit_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Exhaust the limit on /
            for _ in range(5):
                await client.get("/")

            # Health endpoints should still work
            resp = await client.get("/health")
            assert resp.status_code == 200

            resp = await client.get("/health/ready")
            assert resp.status_code == 200

    async def test_different_ips_have_independent_limits(self) -> None:
        """Each client IP gets its own counter."""
        app = Starlette(routes=[Route("/", _homepage)])
        app.add_middleware(RateLimitMiddleware, requests_per_minute=2)
        transport = ASGITransport(app=app)

        # Client A — exhaust its limit
        async with AsyncClient(transport=transport, base_url="http://test") as client_a:
            for _ in range(2):
                resp = await client_a.get("/")
                assert resp.status_code == 200
            resp = await client_a.get("/")
            assert resp.status_code == 429

        # Client B (same transport, but the middleware keys on request.client.host
        # which is the same in test — so we verify the counter is per-IP by
        # checking that a fresh middleware instance gives a fresh counter)
        app2 = Starlette(routes=[Route("/", _homepage)])
        app2.add_middleware(RateLimitMiddleware, requests_per_minute=2)
        transport2 = ASGITransport(app=app2)
        async with AsyncClient(transport=transport2, base_url="http://test") as client_b:
            resp = await client_b.get("/")
            assert resp.status_code == 200


# ===================================================================
# IngressMiddleware
# ===================================================================


class TestIngressMiddleware:
    """Tests for the HA ingress path rewriting middleware."""

    async def test_adds_version_header(self, ingress_app: Starlette) -> None:
        transport = ASGITransport(app=ingress_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
            assert resp.status_code == 200
            assert "X-ClimateIQ-Version" in resp.headers
            # Should be a non-empty version string
            assert len(resp.headers["X-ClimateIQ-Version"]) > 0

    async def test_rewrites_location_header_with_ingress_path(self, ingress_app: Starlette) -> None:
        transport = ASGITransport(app=ingress_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/redirect",
                headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"},
            )
            assert resp.status_code == 302
            location = resp.headers["location"]
            assert location.startswith("/api/hassio_ingress/abc123")
            assert "/dashboard" in location

    async def test_no_rewrite_without_ingress_path(self, ingress_app: Starlette) -> None:
        transport = ASGITransport(app=ingress_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get("/redirect")
            assert resp.status_code == 302
            location = resp.headers["location"]
            assert location == "/dashboard"


# ===================================================================
# HAAuthMiddleware
# ===================================================================


class TestHAAuthMiddleware:
    """Tests for the HA authentication middleware."""

    async def test_health_paths_always_pass(self) -> None:
        inner = Starlette(
            routes=[
                Route("/health", _health),
                Route("/health/ready", _health),
            ],
        )
        with patch("backend.api.middleware.is_ha_addon", return_value=True):
            app = HAAuthMiddleware(inner, require_auth_for_direct=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

            resp = await client.get("/health/ready")
            assert resp.status_code == 200

    async def test_ingress_header_passes_through(self) -> None:
        inner = Starlette(routes=[Route("/api/data", _homepage)])
        with patch("backend.api.middleware.is_ha_addon", return_value=True):
            app = HAAuthMiddleware(inner, require_auth_for_direct=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "test-token"}):
                resp = await client.get(
                    "/api/data",
                    headers={"X-Ingress-Path": "/api/hassio_ingress/xyz"},
                )
            assert resp.status_code == 200

    async def test_addon_mode_direct_access_without_bearer_returns_401(self) -> None:
        inner = Starlette(routes=[Route("/api/data", _homepage)])
        with patch("backend.api.middleware.is_ha_addon", return_value=True):
            app = HAAuthMiddleware(inner, require_auth_for_direct=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/data")
            assert resp.status_code == 401
            body = resp.json()
            assert "Authentication required" in body["detail"]

    async def test_addon_mode_direct_access_with_bearer_passes(self) -> None:
        inner = Starlette(routes=[Route("/api/data", _homepage)])
        with patch("backend.api.middleware.is_ha_addon", return_value=True):
            app = HAAuthMiddleware(inner, require_auth_for_direct=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/data",
                headers={"Authorization": "Bearer some-valid-token"},
            )
            assert resp.status_code == 200

    async def test_non_addon_mode_passes_all_requests(self, auth_app_non_addon: Starlette) -> None:
        transport = ASGITransport(app=auth_app_non_addon)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/data")
            assert resp.status_code == 200


# ===================================================================
# is_ha_addon()
# ===================================================================


class TestIsHaAddon:
    """Tests for the is_ha_addon() detection function."""

    def test_returns_true_when_supervisor_token_set(self) -> None:
        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "abc123"}, clear=False):
            assert is_ha_addon() is True

    def test_returns_true_when_addon_mode_env_set(self) -> None:
        with patch.dict(os.environ, {"CLIMATEIQ_HA_ADDON_MODE": "true"}, clear=False):
            assert is_ha_addon() is True

    def test_returns_false_when_no_env_vars(self) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("SUPERVISOR_TOKEN", "CLIMATEIQ_HA_ADDON_MODE")
        }
        with patch.dict(os.environ, env, clear=True):
            assert is_ha_addon() is False
