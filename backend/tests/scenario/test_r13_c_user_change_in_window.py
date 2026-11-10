from datetime import UTC, datetime, timedelta
from typing import Any

import backend.api.main as main_mod
from backend.core.manual_control import climate_entity
from backend.integrations.ha_client import EntityState
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _Notif, _use_llm

MON = (2026, 11, 9)

def down(house, z="Living Room"):
    eid = f"sensor.{z.lower().replace(' ', '_')}_temperature"
    house.ha.entities[eid] = EntityState(entity_id=eid, state="unavailable", attributes={})

async def test_restart_gap_handback(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    main_mod._last_mode_switch.clear()
    main_mod._mode_switch_loaded = False  # restart
    down(house)
    for h in (17, 18, 19):
        house.at(*MON, h, 1)
        await house.tick_scheduled()
    assert house.ha.mode != "off"

async def test_user_on_then_off_inside_window(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Day", "06:30", "22:00", 72, ["Living Room"])
    async with main_mod.get_session_maker()() as db:
        ent = await climate_entity(db)
    main_mod._mode_switch_loaded = True
    main_mod._last_mode_switch.clear()
    house.at(*MON, 11, 0)
    main_mod._last_mode_switch[ent] = ("off", datetime.now(UTC) - timedelta(seconds=30))
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=68)
    await house.tick_scheduled()          # user turned it on, within window
    house.ha.set_thermostat(mode="off", setpoint_f=72, reading_f=68)  # user turns off again
    down(house)
    house.at(*MON, 11, 3)
    await house.tick_scheduled()
    assert house.ha.mode == "off", "user's off undone"

async def test_unavailable_state(house: House) -> None:
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    house.ha.set_thermostat(mode="unavailable")
    house.at(*MON, 12, 5)
    await house.tick_scheduled()
    rec = main_mod._last_mode_switch
    assert any(v[0]=="off" for v in rec.values())

async def test_active_handback_once(house: House, monkeypatch: Any) -> None:
    _use_llm(monkeypatch, "RECOMMENDED_TEMP: 72\nFAN_ACTIONS:\nREASON: ok")
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    for m in (0, 1, 2, 3, 4, 5):
        house.at(*MON, 12, m)
        await house.tick_all()
    assert house.ha.mode == "off"
    down(house)
    for m in range(0, 20):
        house.at(*MON, 17, m)
        await house.tick_all()
    sw = [c[2]["hvac_mode"] for c in house.ha.calls if c[1] == "set_hvac_mode"]
    print("SW", sw, len(n.sent))
    assert sw.count("off") == 1 and house.ha.mode != "off" and len(sw) == 2

async def test_followme_handback(house: House, monkeypatch: Any) -> None:
    n = _Notif()
    main_mod._notification_service = n  # type: ignore[assignment]
    await house.add_zone("Living Room", 68)
    await house.add_schedule("Away", "09:00", "17:00", 60, ["Living Room"], hvac_mode="off")
    await house.set_mode(SystemMode.follow_me)
    house.ha.set_thermostat(mode="heat", setpoint_f=68, reading_f=68)
    for m in (0, 1, 2, 3):
        house.at(*MON, 12, m)
        await house.tick_all()
    down(house)
    for m in range(0, 10):
        house.at(*MON, 17, m)
        await house.tick_all()
    sw = [c[2]["hvac_mode"] for c in house.ha.calls if c[1] == "set_hvac_mode"]
    print("SW", sw)
    assert len(sw) <= 2 and house.ha.mode != "off"
