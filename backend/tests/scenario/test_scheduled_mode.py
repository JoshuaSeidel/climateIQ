"""Scenario tests: Scheduled mode heat/cool behaviour (docs/CONTROL_SPEC.md)."""

from __future__ import annotations

from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

# 2026-11-09 is a Monday.
MON = (2026, 11, 9)


async def _family_house(house: House) -> None:
    """The real household layout: bedrooms on Sleeping, downstairs on Daytime."""
    for name, temp in [
        ("Master Bedroom", 74.5), ("Olivers Room", 73.9),
        ("Living Room", 68.2), ("Kitchen", 70.5), ("Office", 73.6),
    ]:
        await house.add_zone(name, temp)
    await house.add_schedule("Sleeping", "20:00", "06:30", 69, ["Master Bedroom", "Olivers Room"], priority=10)
    await house.add_schedule("Daytime", "06:30", "18:00", 72, ["Living Room", "Kitchen", "Office"], priority=9)


async def test_sleeping_wins_over_daytime_warmup_before_dawn(house: House) -> None:
    """Spec §7.1 — 2026-11-09 incident: bedrooms at 74°F must not be heated."""
    await _family_house(house)
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=71)
    for minute in (0, 15, 30, 45):
        house.at(*MON, 5, minute)
        await house.tick_scheduled()
        assert not house.ha.would_heat(), f"heating at 05:{minute:02d}: setpoint {house.ha.setpoint_f}"


async def test_rooms_above_target_never_heated(house: House) -> None:
    """Spec §5.3."""
    await house.add_zone("Bedroom", 72)
    await house.add_schedule("Night", "20:00", "06:30", 69, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=67)
    house.at(*MON, 23, 0)
    await house.tick_scheduled()
    assert not house.ha.would_heat()


async def test_cold_rooms_get_heat(house: House) -> None:
    """Spec §5.4 — setpoint strictly above the thermostat's own reading."""
    await house.add_zone("Bedroom", 66)
    await house.add_schedule("Night", "20:00", "06:30", 69, ["Bedroom"])
    house.ha.set_thermostat(mode="heat", setpoint_f=66, reading_f=70)
    house.at(*MON, 23, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "heat"
    assert house.ha.would_heat(), f"setpoint {house.ha.setpoint_f} vs reading 70"


async def test_hot_house_in_heat_mode_switches_to_cool(house: House) -> None:
    """Spec §4.2/§4.5 — wrong direction bypasses the cooldown."""
    await house.add_zone("Living Room", 77)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=67, reading_f=76)
    house.at(*MON, 14, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "cool"
    assert house.ha.would_cool()


async def test_learn_mode_never_writes(house: House) -> None:
    """Spec §2 — Learn hands control to the Ecobee."""
    await house.add_zone("Living Room", 60)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    await house.set_mode(SystemMode.learn)
    house.at(*MON, 6, 30)
    await house.tick_all()
    house.at(*MON, 14, 0)
    await house.tick_all()
    assert house.ha.writes() == []


async def test_overnight_schedule_after_midnight_belongs_to_start_day(house: House) -> None:
    """Spec §3.1 — Friday 20:00-06:30 is still active Saturday 03:00."""
    await house.add_zone("Bedroom", 74)
    await house.add_schedule("Fri night", "20:00", "06:30", 68, ["Bedroom"], days=[4])
    house.ha.set_thermostat(mode="heat", setpoint_f=74, reading_f=72)
    house.at(2026, 11, 14, 3, 0)  # Saturday
    await house.tick_scheduled()
    assert not house.ha.would_heat(), "Friday-night schedule ignored after midnight"


async def test_manual_override_is_not_reverted(house: House) -> None:
    """Spec §2 — an override holds until the next schedule boundary."""
    from backend.api.routes.system import set_manual_override
    from backend.models.database import get_session_maker

    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 70, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 10, 0)
    await house.tick_scheduled()
    async with get_session_maker()() as db:
        await set_manual_override({"temperature": 74}, db)
    assert house.ha.setpoint_f == 74
    for minute in (1, 5, 30):
        house.at(*MON, 10, minute)
        await house.tick_scheduled()
        assert house.ha.setpoint_f == 74, f"override reverted at 10:{minute:02d}"
    # Next boundary (Day ends 18:00 → no schedule; override expires).
    house.at(*MON, 18, 1)
    await house.tick_scheduled()


async def test_follow_me_honours_active_schedule(house: House) -> None:
    """Spec §2 — schedules bind in Follow-Me too."""
    await house.add_zone("Bedroom", 74)
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Night", "20:00", "06:30", 69, ["Bedroom"])
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=70)
    house.at(*MON, 23, 0)
    await house.tick_all()
    assert not house.ha.would_heat()


async def test_idempotent_ticks(house: House) -> None:
    """Spec §9.3 — unchanged inputs → no repeated writes."""
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Day", "06:30", "18:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=69, reading_f=70)
    house.at(*MON, 10, 0)
    await house.tick_scheduled()
    first = len(house.ha.writes())
    house.at(*MON, 10, 1)
    await house.tick_scheduled()
    house.at(*MON, 10, 2)
    await house.tick_scheduled()
    assert len(house.ha.writes()) == first
