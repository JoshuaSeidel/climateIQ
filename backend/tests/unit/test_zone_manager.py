"""Comprehensive unit tests for backend.core.zone_manager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from backend.core.zone_manager import DeviceState, ZoneManager, ZoneState
from backend.models.enums import ControlMethod, DeviceType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(minutes_ago: float = 0) -> datetime:
    """Return a UTC datetime *minutes_ago* minutes in the past."""
    return datetime.now(UTC) - timedelta(minutes=minutes_ago)


def _make_zone(zone_id: UUID | None = None, name: str = "TestZone") -> ZoneState:
    return ZoneState(zone_id=zone_id or uuid4(), name=name)


def _make_device_orm(
    device_id: object = None,
    name: str = "Thermostat",
    device_type: DeviceType = DeviceType.thermostat,
    control_method: ControlMethod = ControlMethod.ha_service_call,
    capabilities: dict[str, bool] | None = None,
) -> MagicMock:
    """Return a mock that quacks like a backend.models.Device ORM instance."""
    mock = MagicMock()
    mock.id = device_id or uuid4()
    mock.name = name
    mock.type = device_type
    mock.control_method = control_method
    mock.capabilities = capabilities or {"supports_temperature": True}
    return mock


# ===================================================================
# DeviceState
# ===================================================================


class TestDeviceState:
    def test_update_merges_payload(self) -> None:
        ds = DeviceState(
            device_id=uuid4(),
            name="Vent",
            type=DeviceType.smart_vent,
            control_method="ha_service_call",
            capabilities={},
            state={"position": 50},
        )
        ds.update({"position": 80, "mode": "auto"})
        assert ds.state["position"] == 80
        assert ds.state["mode"] == "auto"

    def test_update_sets_timestamp(self) -> None:
        ds = DeviceState(
            device_id=uuid4(),
            name="Vent",
            type=DeviceType.smart_vent,
            control_method="ha_service_call",
            capabilities={},
        )
        ts = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
        ds.update({"x": 1}, timestamp=ts)
        assert ds.last_updated == ts

    def test_update_uses_utcnow_when_no_timestamp(self) -> None:
        ds = DeviceState(
            device_id=uuid4(),
            name="Vent",
            type=DeviceType.smart_vent,
            control_method="ha_service_call",
            capabilities={},
        )
        before = datetime.now(UTC)
        ds.update({"x": 1})
        after = datetime.now(UTC)
        assert before <= ds.last_updated <= after


# ===================================================================
# ZoneState — record_temperature
# ===================================================================


class TestZoneStateRecordTemperature:
    def test_first_reading_sets_value_directly(self) -> None:
        zs = _make_zone()
        assert zs.temperature_c is None
        zs.record_temperature(22.0)
        assert zs.temperature_c == 22.0

    def test_exponential_smoothing(self) -> None:
        zs = _make_zone()
        zs.record_temperature(20.0, alpha=0.3)
        zs.record_temperature(30.0, alpha=0.3)
        # EMA: 0.3 * 30 + 0.7 * 20 = 9 + 14 = 23.0
        assert zs.temperature_c == pytest.approx(23.0)

    def test_history_tracking(self) -> None:
        zs = _make_zone()
        ts1 = _ts(10)
        ts2 = _ts(5)
        zs.record_temperature(20.0, timestamp=ts1)
        zs.record_temperature(22.0, timestamp=ts2)
        assert len(zs._temp_history) == 2
        assert zs._temp_history[0][0] == ts1
        assert zs._temp_history[1][0] == ts2

    def test_updates_last_sensor_update(self) -> None:
        zs = _make_zone()
        ts = _ts(3)
        zs.record_temperature(21.0, timestamp=ts)
        assert zs.last_sensor_update == ts

    def test_custom_alpha(self) -> None:
        zs = _make_zone()
        zs.record_temperature(20.0, alpha=0.5)
        zs.record_temperature(30.0, alpha=0.5)
        # 0.5 * 30 + 0.5 * 20 = 25
        assert zs.temperature_c == pytest.approx(25.0)


# ===================================================================
# ZoneState — record_humidity
# ===================================================================


class TestZoneStateRecordHumidity:
    def test_first_reading_sets_value_directly(self) -> None:
        zs = _make_zone()
        assert zs.humidity is None
        zs.record_humidity(50.0)
        assert zs.humidity == 50.0

    def test_exponential_smoothing(self) -> None:
        zs = _make_zone()
        zs.record_humidity(40.0, alpha=0.3)
        zs.record_humidity(60.0, alpha=0.3)
        # 0.3 * 60 + 0.7 * 40 = 18 + 28 = 46
        assert zs.humidity == pytest.approx(46.0)

    def test_history_tracking(self) -> None:
        zs = _make_zone()
        ts1 = _ts(10)
        ts2 = _ts(5)
        zs.record_humidity(40.0, timestamp=ts1)
        zs.record_humidity(50.0, timestamp=ts2)
        assert len(zs._humidity_history) == 2
        assert zs._humidity_history[0][0] == ts1

    def test_updates_last_sensor_update(self) -> None:
        zs = _make_zone()
        ts = _ts(2)
        zs.record_humidity(55.0, timestamp=ts)
        assert zs.last_sensor_update == ts


# ===================================================================
# ZoneState — record_occupancy
# ===================================================================


class TestZoneStateRecordOccupancy:
    def test_first_occupancy_sets_value_and_change_time(self) -> None:
        zs = _make_zone()
        ts = _ts(1)
        zs.record_occupancy(True, timestamp=ts)
        assert zs.occupancy is True
        assert zs.last_occupancy_change == ts

    def test_same_occupancy_does_not_update_change_time(self) -> None:
        zs = _make_zone()
        ts1 = _ts(10)
        ts2 = _ts(5)
        zs.record_occupancy(True, timestamp=ts1)
        zs.record_occupancy(True, timestamp=ts2)
        # last_occupancy_change should stay at ts1 because no transition
        assert zs.last_occupancy_change == ts1

    def test_transition_updates_change_time(self) -> None:
        zs = _make_zone()
        ts1 = _ts(10)
        ts2 = _ts(5)
        zs.record_occupancy(True, timestamp=ts1)
        zs.record_occupancy(False, timestamp=ts2)
        assert zs.last_occupancy_change == ts2
        assert zs.occupancy is False

    def test_updates_last_sensor_update(self) -> None:
        zs = _make_zone()
        ts = _ts(1)
        zs.record_occupancy(False, timestamp=ts)
        assert zs.last_sensor_update == ts


# ===================================================================
# ZoneState — temp_trend_c_per_hour / humidity_trend_per_hour
# ===================================================================


class TestZoneStateTrends:
    def test_temp_trend_returns_none_with_fewer_than_two_samples(self) -> None:
        zs = _make_zone()
        assert zs.temp_trend_c_per_hour() is None
        zs.record_temperature(20.0, timestamp=_ts(5))
        assert zs.temp_trend_c_per_hour() is None

    def test_temp_trend_calculates_correctly(self) -> None:
        zs = _make_zone()
        # 30 min apart, 2°C rise → 4°C/hour
        ts_start = _ts(30)
        ts_end = _ts(0)
        zs.record_temperature(20.0, timestamp=ts_start)
        zs.record_temperature(22.0, timestamp=ts_end)
        # After smoothing: first=20, second=0.3*22+0.7*20=20.6
        # Trend = (20.6 - 20.0) / 30 * 60 = 1.2
        trend = zs.temp_trend_c_per_hour()
        assert trend is not None
        assert trend == pytest.approx(1.2, abs=0.01)

    def test_temp_trend_ignores_old_samples(self) -> None:
        zs = _make_zone()
        # Sample outside the 90-min lookback
        zs.record_temperature(10.0, timestamp=_ts(120))
        zs.record_temperature(20.0, timestamp=_ts(5))
        # Only 1 sample within lookback → None
        assert zs.temp_trend_c_per_hour() is None

    def test_humidity_trend_returns_none_with_fewer_than_two_samples(self) -> None:
        zs = _make_zone()
        assert zs.humidity_trend_per_hour() is None
        zs.record_humidity(50.0, timestamp=_ts(5))
        assert zs.humidity_trend_per_hour() is None

    def test_humidity_trend_calculates_correctly(self) -> None:
        zs = _make_zone()
        ts_start = _ts(60)
        ts_end = _ts(0)
        zs.record_humidity(40.0, timestamp=ts_start)
        zs.record_humidity(50.0, timestamp=ts_end)
        # After smoothing: first=40, second=0.3*50+0.7*40=43
        # Trend = (43 - 40) / 60 * 60 = 3.0
        trend = zs.humidity_trend_per_hour()
        assert trend is not None
        assert trend == pytest.approx(3.0, abs=0.01)


# ===================================================================
# ZoneState — set_metric / push_flag
# ===================================================================


class TestZoneStateMetricsAndFlags:
    def test_set_metric(self) -> None:
        zs = _make_zone()
        zs.set_metric("target_temperature_c", 22.0)
        assert zs.metrics["target_temperature_c"] == 22.0

    def test_set_metric_overwrites(self) -> None:
        zs = _make_zone()
        zs.set_metric("co2", 400.0)
        zs.set_metric("co2", 800.0)
        assert zs.metrics["co2"] == 800.0

    def test_push_flag_active(self) -> None:
        zs = _make_zone()
        zs.push_flag("stale", active=True)
        assert "stale" in zs.attention_flags

    def test_push_flag_inactive_removes(self) -> None:
        zs = _make_zone()
        zs.push_flag("stale", active=True)
        zs.push_flag("stale", active=False)
        assert "stale" not in zs.attention_flags

    def test_push_flag_inactive_noop_when_absent(self) -> None:
        zs = _make_zone()
        zs.push_flag("nonexistent", active=False)
        assert "nonexistent" not in zs.attention_flags


# ===================================================================
# ZoneState — register_device
# ===================================================================


class TestZoneStateRegisterDevice:
    def test_creates_device_state_from_orm_model(self) -> None:
        zs = _make_zone()
        mock_device = _make_device_orm(name="Living Room Thermostat")
        zs.register_device(mock_device)
        assert mock_device.id in zs.devices
        ds = zs.devices[mock_device.id]
        assert ds.name == "Living Room Thermostat"
        assert ds.device_id == mock_device.id
        assert ds.capabilities == {"supports_temperature": True}

    def test_control_method_enum_value_extracted(self) -> None:
        zs = _make_zone()
        mock_device = _make_device_orm(control_method=ControlMethod.ha_service_call)
        zs.register_device(mock_device)
        ds = zs.devices[mock_device.id]
        assert ds.control_method == "ha_service_call"

    def test_control_method_string_fallback(self) -> None:
        zs = _make_zone()
        mock_device = _make_device_orm()
        mock_device.control_method = "mqtt_direct"
        zs.register_device(mock_device)
        ds = zs.devices[mock_device.id]
        assert ds.control_method == "mqtt_direct"

    def test_none_capabilities_becomes_empty_dict(self) -> None:
        zs = _make_zone()
        mock_device = _make_device_orm(capabilities=None)
        mock_device.capabilities = None
        zs.register_device(mock_device)
        ds = zs.devices[mock_device.id]
        assert ds.capabilities == {}


# ===================================================================
# ZoneManager — __init__ alpha clamping
# ===================================================================


class TestZoneManagerInit:
    def test_default_alpha(self) -> None:
        zm = ZoneManager()
        assert zm._alpha == 0.3

    def test_alpha_clamped_low(self) -> None:
        zm = ZoneManager(smoothing_alpha=0.001)
        assert zm._alpha == 0.05

    def test_alpha_clamped_high(self) -> None:
        zm = ZoneManager(smoothing_alpha=5.0)
        assert zm._alpha == 1.0

    def test_alpha_within_range(self) -> None:
        zm = ZoneManager(smoothing_alpha=0.5)
        assert zm._alpha == 0.5

    def test_alpha_at_boundary_low(self) -> None:
        zm = ZoneManager(smoothing_alpha=0.05)
        assert zm._alpha == 0.05

    def test_alpha_at_boundary_high(self) -> None:
        zm = ZoneManager(smoothing_alpha=1.0)
        assert zm._alpha == 1.0


# ===================================================================
# ZoneManager — update_from_sensor_payload
# ===================================================================


class TestZoneManagerUpdateFromSensorPayload:
    async def test_creates_zone_if_not_exists(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id, zone_name="Kitchen", temperature_c=22.0
        )
        assert state.zone_id == zone_id
        assert state.name == "Kitchen"
        assert state.temperature_c == 22.0

    async def test_updates_existing_zone(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        await zm.update_from_sensor_payload(
            zone_id=zone_id, zone_name="Kitchen", temperature_c=20.0
        )
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id, zone_name="Kitchen", temperature_c=25.0
        )
        # EMA: 0.3 * 25 + 0.7 * 20 = 7.5 + 14 = 21.5
        assert state.temperature_c == pytest.approx(21.5)

    async def test_updates_humidity(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id, zone_name="Bath", humidity=60.0
        )
        assert state.humidity == 60.0

    async def test_updates_occupancy(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id, zone_name="Office", occupancy=True
        )
        assert state.occupancy is True

    async def test_sets_metrics(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Lab",
            metrics={"co2": 450.0, "voc": 12.0},
        )
        assert state.metrics["co2"] == 450.0
        assert state.metrics["voc"] == 12.0

    async def test_calculates_comfort_score(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        # First set a target so comfort score has something to work with
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Bedroom",
            temperature_c=22.0,
            metrics={"target_temperature_c": 22.0},
        )
        # Perfect temp match → component = 1.0
        assert state.comfort_score > 0.0

    async def test_none_values_are_skipped(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Empty",
            temperature_c=None,
            humidity=None,
            occupancy=None,
        )
        assert state.temperature_c is None
        assert state.humidity is None
        assert state.occupancy is None


# ===================================================================
# ZoneManager — update_device_state
# ===================================================================


class TestZoneManagerUpdateDeviceState:
    async def test_creates_zone_and_device_if_not_exists(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        device_id = uuid4()
        ds = await zm.update_device_state(
            zone_id=zone_id,
            device_id=device_id,
            device_name="Thermostat",
            device_type=DeviceType.thermostat,
            control_method="ha_service_call",
            capabilities={"supports_temperature": True},
            state_payload={"mode": "heat"},
        )
        assert ds.device_id == device_id
        assert ds.state["mode"] == "heat"
        # Zone was auto-created
        assert zm.get_state(zone_id) is not None

    async def test_updates_existing_device(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        device_id = uuid4()
        await zm.update_device_state(
            zone_id=zone_id,
            device_id=device_id,
            device_name="Vent",
            device_type=DeviceType.smart_vent,
            control_method="ha_service_call",
            state_payload={"position": 50},
        )
        ds = await zm.update_device_state(
            zone_id=zone_id,
            device_id=device_id,
            device_name="Vent",
            device_type=DeviceType.smart_vent,
            control_method="ha_service_call",
            state_payload={"position": 80},
        )
        assert ds.state["position"] == 80

    async def test_no_payload_leaves_state_empty(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        device_id = uuid4()
        ds = await zm.update_device_state(
            zone_id=zone_id,
            device_id=device_id,
            device_name="Fan",
            device_type=DeviceType.fan,
            control_method="ha_service_call",
        )
        assert ds.state == {}


# ===================================================================
# ZoneManager — snapshot
# ===================================================================


class TestZoneManagerSnapshot:
    async def test_returns_cloned_list(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        await zm.update_from_sensor_payload(zone_id=zone_id, zone_name="Room", temperature_c=21.0)
        snap = await zm.snapshot()
        assert len(snap) == 1
        assert snap[0].zone_id == zone_id
        # Verify it's a clone — mutating the snapshot shouldn't affect the manager
        snap[0].name = "MUTATED"
        original = zm.get_state(zone_id)
        assert original is not None
        assert original.name == "Room"

    async def test_empty_snapshot(self) -> None:
        zm = ZoneManager()
        snap = await zm.snapshot()
        assert snap == []

    async def test_snapshot_preserves_device_data(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        device_id = uuid4()
        await zm.update_device_state(
            zone_id=zone_id,
            device_id=device_id,
            device_name="Thermo",
            device_type=DeviceType.thermostat,
            control_method="ha_service_call",
            state_payload={"temp": 22},
        )
        snap = await zm.snapshot()
        assert device_id in snap[0].devices
        assert snap[0].devices[device_id].state["temp"] == 22


# ===================================================================
# ZoneManager — zones_needing_attention
# ===================================================================


class TestZoneManagerZonesNeedingAttention:
    async def test_temperature_delta_flags_zone(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Hot Room",
            temperature_c=26.0,
            metrics={"target_temperature_c": 22.0},
        )
        flagged = zm.zones_needing_attention(temperature_delta=2.0)
        assert len(flagged) == 1
        assert "temperature" in flagged[0].attention_flags

    async def test_humidity_delta_flags_zone(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Humid Room",
            humidity=75.0,
            metrics={"target_humidity": 50.0},
        )
        flagged = zm.zones_needing_attention(humidity_delta=12.0)
        assert len(flagged) == 1
        assert "humidity" in flagged[0].attention_flags

    async def test_stale_sensor_flags_zone(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Stale Room",
            temperature_c=21.0,
        )
        # Manually backdate the sensor update
        state.last_sensor_update = _ts(30)
        flagged = zm.zones_needing_attention(stale_minutes=20)
        assert len(flagged) == 1
        assert "stale" in flagged[0].attention_flags

    async def test_no_flags_when_within_bounds(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Comfy Room",
            temperature_c=22.0,
            humidity=50.0,
            metrics={"target_temperature_c": 22.0, "target_humidity": 50.0},
        )
        flagged = zm.zones_needing_attention()
        assert len(flagged) == 0

    async def test_no_target_means_no_temperature_flag(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="No Target",
            temperature_c=30.0,
        )
        flagged = zm.zones_needing_attention(stale_minutes=9999)
        assert len(flagged) == 0

    async def test_multiple_flags_on_same_zone(self) -> None:
        zm = ZoneManager()
        zone_id = uuid4()
        state = await zm.update_from_sensor_payload(
            zone_id=zone_id,
            zone_name="Bad Room",
            temperature_c=30.0,
            humidity=90.0,
            metrics={"target_temperature_c": 22.0, "target_humidity": 50.0},
        )
        state.last_sensor_update = _ts(30)
        flagged = zm.zones_needing_attention(
            temperature_delta=2.0, humidity_delta=12.0, stale_minutes=20
        )
        assert len(flagged) == 1
        flags = flagged[0].attention_flags
        assert "temperature" in flags
        assert "humidity" in flags
        assert "stale" in flags


# ===================================================================
# ZoneManager — _calculate_comfort_score
# ===================================================================


class TestZoneManagerCalculateComfortScore:
    def test_no_targets_returns_zero(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.temperature_c = 22.0
        assert zm._calculate_comfort_score(zs) == 0.0

    def test_perfect_temperature_match(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.temperature_c = 22.0
        zs.metrics["target_temperature_c"] = 22.0
        score = zm._calculate_comfort_score(zs)
        # component = max(0, 1 - 0/5) = 1.0 → 100.0
        assert score == pytest.approx(100.0)

    def test_temperature_5c_off_gives_zero_component(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.temperature_c = 27.0
        zs.metrics["target_temperature_c"] = 22.0
        score = zm._calculate_comfort_score(zs)
        # component = max(0, 1 - 5/5) = 0.0 → 0.0
        assert score == pytest.approx(0.0)

    def test_humidity_component(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.humidity = 50.0
        zs.metrics["target_humidity"] = 50.0
        score = zm._calculate_comfort_score(zs)
        # component = 1.0 → 100.0
        assert score == pytest.approx(100.0)

    def test_occupancy_occupied_component(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.occupancy = True
        score = zm._calculate_comfort_score(zs)
        # component = 1.0 → 100.0
        assert score == pytest.approx(100.0)

    def test_occupancy_unoccupied_component(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.occupancy = False
        score = zm._calculate_comfort_score(zs)
        # component = 0.85 → 85.0
        assert score == pytest.approx(85.0)

    def test_combined_components_averaged(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.temperature_c = 22.0
        zs.humidity = 50.0
        zs.occupancy = True
        zs.metrics["target_temperature_c"] = 22.0
        zs.metrics["target_humidity"] = 50.0
        score = zm._calculate_comfort_score(zs)
        # components: [1.0, 1.0, 1.0] → mean=1.0 → 100.0
        assert score == pytest.approx(100.0)

    def test_partial_delta_score(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.temperature_c = 24.5
        zs.metrics["target_temperature_c"] = 22.0
        score = zm._calculate_comfort_score(zs)
        # delta=2.5, component = max(0, 1 - 2.5/5) = 0.5 → 50.0
        assert score == pytest.approx(50.0)

    def test_humidity_partial_delta(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.humidity = 60.0
        zs.metrics["target_humidity"] = 50.0
        score = zm._calculate_comfort_score(zs)
        # delta=10, component = max(0, 1 - 10/20) = 0.5 → 50.0
        assert score == pytest.approx(50.0)

    def test_mixed_good_and_bad(self) -> None:
        zm = ZoneManager()
        zs = _make_zone()
        zs.temperature_c = 22.0  # perfect
        zs.humidity = 70.0  # 20 off → component = 0.0
        zs.occupancy = True  # component = 1.0
        zs.metrics["target_temperature_c"] = 22.0
        zs.metrics["target_humidity"] = 50.0
        score = zm._calculate_comfort_score(zs)
        # components: [1.0, 0.0, 1.0] → mean = 2/3 ≈ 0.6667 → 66.7
        assert score == pytest.approx(66.7, abs=0.1)
