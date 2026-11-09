"""§2 Learn: if ClimateIQ itself switched the Ecobee off, entering Learn must
put it back in heat_cool/auto so its program can run.  That knowledge
(_last_mode_switch) is in memory only, so after an add-on restart the Ecobee
stays OFF in Learn."""
import backend.api.main as main_mod
from backend.api.routes.system import apply_system_mode
from backend.models.database import get_session_maker
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)


async def test_learn_restores_mode_after_restart(house: House) -> None:
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Away", "09:00", "17:00", 62, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off", "precondition: the off schedule turned it off"

    # Add-on restart (update / HA host reboot): in-memory state is gone.
    main_mod._last_mode_switch.clear()
    main_mod._runtime_state_loaded = False
    main_mod._mode_switch_loaded = False
    main_mod._last_offset_temp.clear()

    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    assert house.ha.mode in ("heat_cool", "auto"), (
        f"Learn after restart left the Ecobee '{house.ha.mode}' although ClimateIQ turned it off"
    )


async def test_learn_restores_mode_without_restart_control(house: House) -> None:
    """Control: same flow without a restart works."""
    await house.add_zone("Living Room", 70)
    await house.add_schedule("Away", "09:00", "17:00", 62, ["Living Room"], hvac_mode="off")
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "off"
    async with get_session_maker()() as db:
        await apply_system_mode(db, SystemMode.learn)
    assert house.ha.mode in ("heat_cool", "auto")


async def test_reversal_cooldown_survives_restart(house: House) -> None:
    """§4.5: the 30-min heat↔cool cooldown is lost on restart (_last_mode_switch)."""
    await house.add_zone("Living Room", 74)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=73)  # idle heat
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "cool"
    # 5 min later the rooms read cold (sun went behind clouds, sensor dip).
    house.set_room("Living Room", 70)
    house.ha.set_thermostat(reading_f=71)
    house.ha.entities["climate.thermostat"].attributes["hvac_action"] = "idle"
    main_mod._last_mode_switch.clear()  # add-on restart in between
    main_mod._runtime_state_loaded = False
    main_mod._mode_switch_loaded = False
    house.at(*MON, 12, 6)
    await house.tick_scheduled()
    assert house.ha.mode == "cool", "cool→heat reversal 6 min after heat→cool (cooldown lost on restart)"


async def test_reversal_cooldown_control_no_restart(house: House) -> None:
    """§4.5: the 30-min heat↔cool cooldown is lost on restart (_last_mode_switch)."""
    await house.add_zone("Living Room", 74)
    await house.add_schedule("Day", "06:00", "22:00", 72, ["Living Room"])
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=73)  # idle heat
    house.at(*MON, 12, 0)
    await house.tick_scheduled()
    assert house.ha.mode == "cool"
    # 5 min later the rooms read cold (sun went behind clouds, sensor dip).
    house.set_room("Living Room", 70)
    house.ha.set_thermostat(reading_f=71)
    house.ha.entities["climate.thermostat"].attributes["hvac_action"] = "idle"
    main_mod._runtime_state_loaded = False
    main_mod._mode_switch_loaded = False
    house.at(*MON, 12, 6)
    await house.tick_scheduled()
    assert house.ha.mode == "cool", "cool→heat reversal 6 min after heat→cool (cooldown lost on restart)"
