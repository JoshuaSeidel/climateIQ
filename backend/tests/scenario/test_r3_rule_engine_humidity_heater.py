"""§2: the rule engine must not interfere with heating/cooling.  A high-
humidity reading in a zone with no dehumidifier makes _select_device fall
back to the zone's first device and `turn_on` it — here a space heater, in
a house that is cooling."""
from datetime import UTC, datetime

import backend.api.main as main_mod
from backend.core.rule_engine import RuleEngine
from backend.core.zone_manager import ZoneManager
from backend.integrations.ha_client import EntityState
from backend.models.database import Device, get_session_maker
from backend.models.enums import ControlMethod, DeviceType, SystemMode
from backend.tests.scenario.harness import House


async def test_humid_room_turns_on_space_heater(house: House) -> None:
    zid = await house.add_zone("Nursery", 74)
    async with get_session_maker()() as db:
        db.add(Device(
            zone_id=zid, name="Nursery heater", type=DeviceType.space_heater,
            control_method=ControlMethod.ha_service_call, ha_entity_id="switch.nursery_heater",
        ))
        await db.commit()
    house.ha.entities["switch.nursery_heater"] = EntityState(
        entity_id="switch.nursery_heater", state="off", attributes={},
    )
    house.ha.set_thermostat(mode="cool", setpoint_f=72, reading_f=75)
    await house.set_mode(SystemMode.scheduled)

    zm = ZoneManager()
    async with get_session_maker()() as db:
        await zm.refresh_from_db(db)
    st = zm.get_state(zid)
    assert st is not None
    st.set_metric("target_humidity", 45.0)
    # temperature inside its band, humidity high (a muggy summer day)
    st.set_metric("comfort_min_c", 21.0)
    st.set_metric("comfort_max_c", 24.0)
    await zm.update_from_sensor_payload(
        zone_id=zid, zone_name="Nursery", temperature_c=23.3, humidity=62.0,
        occupancy=None, timestamp=datetime.now(UTC),
    )
    old_zm, old_re = main_mod.app_state.zone_manager, main_mod.app_state.rule_engine
    main_mod.app_state.zone_manager, main_mod.app_state.rule_engine = zm, RuleEngine()
    try:
        await main_mod.execute_rule_engine()
    finally:
        main_mod.app_state.zone_manager, main_mod.app_state.rule_engine = old_zm, old_re
    heater_calls = [c for c in house.ha.calls if c[2].get("entity_id") == "switch.nursery_heater"]
    assert not heater_calls, f"rule engine switched the space heater on for humidity: {heater_calls}"
