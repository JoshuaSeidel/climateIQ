"""Fixtures for scenario tests (real loops, simulated house, real Postgres)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from backend.tests.conftest import _require_db
from backend.tests.scenario.harness import House


@pytest.fixture
async def house() -> AsyncGenerator[House]:
    _require_db()
    h = House()
    await h.reset()
    try:
        yield h
    finally:
        h.stop_clock()
        import backend.api.dependencies as deps

        deps._ha_client = None
