"""Windows: cool escape valve + forbidden direction + stuck states."""
from backend.tests.scenario.harness import House, f_to_c

MON = (2026, 11, 9)


async def _win(house, **kw):
    w = {"name": "Day", "start_time": "08:00", "end_time": "20:00", "allow_cool": False, "allow_heat": True}
    w.update(kw)
    await house.set_setting("hvac_time_windows", {"enabled": True, "windows": [w]})


async def test_cool_valve_opens(house: House) -> None:
    await house.add_zone("Bed", 82)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    await _win(house, escape_above_c=f_to_c(80))
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=75)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print("mode", house.ha.mode, house.ha.setpoint_f)
    assert house.ha.mode == "cool" and house.ha.would_cool()


async def test_cool_valve_closed_no_escape(house: House) -> None:
    await house.add_zone("Bed", 78)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    await _win(house, escape_above_c=f_to_c(80))
    house.ha.set_thermostat(mode="cool", setpoint_f=70, reading_f=76)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    print("mode", house.ha.mode, house.ha.setpoint_f)
    assert not house.ha.would_cool()


async def test_cool_valve_hysteresis_and_close(house: House) -> None:
    await house.add_zone("Bed", 82)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    await _win(house, escape_above_c=f_to_c(80))
    house.ha.set_thermostat(mode="cool", setpoint_f=70, reading_f=76)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.would_cool()
    house.set_room("Bed", 79.5)
    house.at(*MON, 12, 1)
    await house.tick_scheduled()
    assert house.ha.would_cool(), "latched"
    house.set_room("Bed", 78.0)
    house.at(*MON, 12, 2)
    await house.tick_scheduled()
    print("sp", house.ha.setpoint_f)
    assert not house.ha.would_cool(), "closed after 1F recovery"


async def test_stale_latch_next_day(house: House) -> None:
    """Latch opened on day 1 (82F). Window ends at 20:00. Next day, rooms 79.5F
    (never reached 80F today) -> cool must stay blocked."""
    await house.add_zone("Bed", 82)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Bed"])
    await _win(house, escape_above_c=f_to_c(80))
    house.ha.set_thermostat(mode="cool", setpoint_f=70, reading_f=76)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.would_cool()
    # evening: window over, rooms cool down to 74 (no window active -> not evaluated)
    house.set_room("Bed", 74)
    house.at(*MON, 21, 0)
    await house.tick_scheduled()
    house.set_room("Bed", 79.5)
    house.ha.set_thermostat(mode="cool", setpoint_f=80, reading_f=76)
    house.at(2026, 11, 10, 12, 0)
    await house.tick_scheduled()
    print("sp", house.ha.setpoint_f, "latched", list(__import__("backend.core.mode_windows", fromlist=["x"])._escape_latched))
    assert not house.ha.would_cool(), "stale latch from yesterday keeps cool escape open at 79.5F"
