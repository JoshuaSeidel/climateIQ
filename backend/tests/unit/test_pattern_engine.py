from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.pattern_engine import OccupancyReading, PatternEngine


@pytest.mark.asyncio
async def test_pattern_engine_occupancy_learning(db_session: AsyncSession) -> None:
    engine = PatternEngine(db_session)
    now = datetime.now(UTC)
    readings = [
        OccupancyReading(
            zone_id="zone-1", timestamp=now - timedelta(minutes=5 * i), occupied=i % 2 == 0
        )
        for i in range(20)
    ]

    result = await engine.learn_occupancy_patterns("zone-1", readings)
    assert isinstance(result, dict)
    assert any(k.startswith(now.strftime("%a").lower()) for k in result.keys())
