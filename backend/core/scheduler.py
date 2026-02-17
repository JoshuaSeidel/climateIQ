"""Schedule parsing and lookup utilities for ClimateIQ."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.config import SETTINGS


@dataclass(slots=True)
class ScheduleEntry:
    day: str
    period: str
    start: int  # minutes since midnight
    end: int
    heat_c: float
    cool_c: float


class Scheduler:
    """Resolve schedules into active periods and targets."""

    PERIOD_ORDER = ("wake", "home", "away", "sleep")

    def __init__(self, schedule_json: str | dict[str, Any]) -> None:
        if isinstance(schedule_json, str):
            parsed = json.loads(schedule_json)
        else:
            parsed = schedule_json
        self._entries = self.parse_schedule(parsed)

    def parse_schedule(self, schedule_json: dict[str, Any]) -> list[ScheduleEntry]:
        entries: list[ScheduleEntry] = []
        for day, periods in schedule_json.items():
            for period_name, payload in periods.items():
                start = self._parse_time(payload.get("start", "06:00"))
                duration = payload.get("duration", 120)
                entry = ScheduleEntry(
                    day=day.lower(),
                    period=period_name,
                    start=start,
                    end=start + duration,
                    heat_c=float(
                        payload.get(
                            "heat_c", payload.get("target_c", SETTINGS.default_comfort_temp_min_c)
                        )
                    ),
                    cool_c=float(
                        payload.get(
                            "cool_c", payload.get("target_c", SETTINGS.default_comfort_temp_max_c)
                        )
                    ),
                )
                entries.append(entry)
        self._entries = entries
        return entries

    def get_current_period(
        self, zone_id: str, *, now: datetime | None = None
    ) -> ScheduleEntry | None:
        now = now or datetime.now(UTC)
        day = self.handle_day_of_week(now)
        minutes = now.hour * 60 + now.minute
        todays = [entry for entry in self._entries if entry.day == day]
        todays.sort(key=lambda e: e.start)
        for entry in todays:
            if entry.start <= minutes < entry.end:
                return entry
        return todays[-1] if todays else None

    def get_target_temperature(
        self,
        zone_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[float, float]:
        period = self.get_current_period(zone_id, now=now)
        if not period:
            return (SETTINGS.default_comfort_temp_min_c, SETTINGS.default_comfort_temp_max_c)
        return (period.heat_c, period.cool_c)

    @staticmethod
    def handle_day_of_week(now: datetime) -> str:
        weekday = now.strftime("%a").lower()
        if weekday in ("sat", "sun"):
            return "weekend"
        return "weekday"

    @staticmethod
    def _parse_time(value: str) -> int:
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)


__all__ = ["ScheduleEntry", "Scheduler"]
