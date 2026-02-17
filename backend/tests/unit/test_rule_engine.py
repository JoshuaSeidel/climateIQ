from uuid import uuid4

from backend.core.rule_engine import RuleEngine
from backend.core.zone_manager import DeviceState, ZoneState
from backend.models.enums import DeviceType


def test_rule_engine_comfort_band_triggers_action() -> None:
    engine = RuleEngine(comfort_c_delta=0.5)
    zone = ZoneState(zone_id=uuid4(), name="Test")
    zone.metrics["target_temperature_c"] = 22.0
    zone.temperature_c = 20.0
    zone.devices[uuid4()] = DeviceState(
        device_id=uuid4(),
        name="Thermostat",
        type=DeviceType.thermostat,
        control_method="ha_service_call",
        capabilities={"supports_temperature": True},
    )

    action = engine.check_comfort_band(zone, {"temperature_c": 20.0})
    assert action is not None
    temp_value = action.parameters["temperature"]
    assert isinstance(temp_value, (int, float)) and not isinstance(temp_value, bool)
    assert temp_value >= 21.5
