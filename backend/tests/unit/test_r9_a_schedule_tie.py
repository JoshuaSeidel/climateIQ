from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from backend.core.schedule_guard import find_active_schedule


def mk(i, **k):
    d = {"id": i, "is_enabled": True, "days_of_week": list(range(7)), "start_time": "06:00",
         "end_time": "10:00", "priority": 5}
    d.update(k)
    return NS(**d)

def test_identical_priority_and_start_is_order_independent():
    a, b = mk("a"), mk("b")
    now = datetime(2026, 11, 9, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    assert find_active_schedule([a, b], now).id == find_active_schedule([b, a], now).id
