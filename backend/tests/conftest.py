import asyncio
import os
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from backend.api.main import app  # noqa: E402
from backend.models.database import close_db, get_session_maker, init_db  # noqa: E402

# Track whether the DB was successfully initialised so downstream fixtures
# can skip gracefully when PostgreSQL is not available (e.g. pure unit-test
# runs on a developer machine without a local database).
_db_available = False


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_db() -> AsyncGenerator[None]:
    global _db_available
    os.environ.setdefault("CLIMATEIQ_DB_NAME", "climateiq_test")
    try:
        await init_db()
        _db_available = True
    except Exception:
        # No PostgreSQL reachable — allow pure unit tests to continue.
        _db_available = False
    yield
    if _db_available:
        await close_db()


def _require_db() -> None:
    """Raise ``pytest.skip`` when the database is unreachable."""
    if not _db_available:
        pytest.skip("PostgreSQL is not available")


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    _require_db()
    session_maker = get_session_maker()
    async with session_maker() as session:
        yield session


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    _require_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
