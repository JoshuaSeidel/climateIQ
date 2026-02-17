from typing import Any

import pytest


@pytest.mark.asyncio
async def test_health_check(client: Any) -> None:
    response = await client.get("/api/v1/system/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_version(client: Any) -> None:
    response = await client.get("/api/v1/system/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
