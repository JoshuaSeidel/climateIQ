from datetime import datetime

from backend.core.scheduler import Scheduler


def test_scheduler_selects_period() -> None:
    schedule = {
        "weekday": {
            "wake": {"start": "06:00", "duration": 180, "target_c": 21},
            "away": {"start": "09:00", "duration": 480, "target_c": 18},
            "home": {"start": "17:00", "duration": 300, "target_c": 21},
            "sleep": {"start": "22:00", "duration": 480, "target_c": 19},
        }
    }
    scheduler = Scheduler(schedule)
    now = datetime(2026, 2, 17, 7, 30)  # Tuesday
    period = scheduler.get_current_period("zone-1", now=now)
    assert period is not None
    assert period.period == "wake"
