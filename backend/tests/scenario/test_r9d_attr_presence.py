"""Attribute-based presence: string 'unavailable'/'unknown'/'detected' mis-parsed as vacant."""
from backend.integrations.ha_websocket import HAWebSocketClient


def _parse(entity, state, attrs):
    c = HAWebSocketClient("http://x", "t")
    return c._parse_state_change(entity, entity.split(".")[0], {"state": state, "attributes": attrs})


def test_attr_unknown_is_not_vacant():
    ch = _parse("sensor.office_multisensor", "22.1", {"occupancy": "unknown"})
    assert ch.presence is None, f"presence={ch.presence!r}: 'unknown' attribute stored as vacant"


def test_attr_detected_is_occupied():
    ch = _parse("sensor.office_multisensor", "22.1", {"motion": "detected"})
    assert ch.presence is True, f"presence={ch.presence!r}"
