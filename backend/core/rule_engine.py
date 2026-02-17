"""Deterministic rule engine for ClimateIQ control decisions."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.core.zone_manager import DeviceState, ZoneState
from backend.models.enums import ActionType, DeviceType, TriggerType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ControlAction:
    """Normalized representation of an action the system can execute."""

    zone_id: str
    device_id: str | None
    action_type: ActionType
    triggered_by: TriggerType
    parameters: dict[str, float | int | str | bool]
    reason: str


class RuleEngine:
    """Evaluate low-latency rule-based decisions before invoking LLMs."""

    def __init__(self, *, comfort_c_delta: float = 1.0, humidity_delta: float = 8.0) -> None:
        self._comfort_delta = comfort_c_delta
        self._humidity_delta = humidity_delta

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def check_comfort_band(
        self, zone: ZoneState, reading: dict[str, float | bool]
    ) -> ControlAction | None:
        """Return action if zone is outside its comfort band.

        Uses zone metrics `target_temperature_c` and `target_humidity` when present.
        When no direct target exists, fall back to `comfort_min_c/comfort_max_c`.
        """

        raw_temp = reading.get("temperature_c")
        raw_humidity = reading.get("humidity")
        temperature = (
            float(raw_temp)
            if isinstance(raw_temp, (int, float)) and not isinstance(raw_temp, bool)
            else None
        )
        humidity = (
            float(raw_humidity)
            if isinstance(raw_humidity, (int, float)) and not isinstance(raw_humidity, bool)
            else None
        )
        target_temp = zone.metrics.get("target_temperature_c")
        target_humidity = zone.metrics.get("target_humidity")
        comfort_min = zone.metrics.get(
            "comfort_min_c", target_temp - self._comfort_delta if target_temp else None
        )
        comfort_max = zone.metrics.get(
            "comfort_max_c", target_temp + self._comfort_delta if target_temp else None
        )

        # Temperature band enforcement
        if temperature is not None and comfort_min is not None and comfort_max is not None:
            if temperature < comfort_min:
                return self._build_action(
                    zone, colder=True, reading_temp=temperature, target=comfort_min
                )
            if temperature > comfort_max:
                return self._build_action(
                    zone, colder=False, reading_temp=temperature, target=comfort_max
                )

        # Humidity adjustments stick to humidifier/dehumidifier where available
        if humidity is not None and target_humidity is not None:
            delta = humidity - target_humidity
            if abs(delta) >= self._humidity_delta:
                preferred_type = DeviceType.dehumidifier if delta > 0 else DeviceType.humidifier
                device = self._select_device(zone, preferred={preferred_type})
                if device:
                    reason = "humidity high" if delta > 0 else "humidity low"
                    params: dict[str, float | int | str | bool] = {
                        "mode": "dehumidify" if delta > 0 else "humidify",
                        "delta": abs(delta),
                    }
                    return ControlAction(
                        zone_id=str(zone.zone_id),
                        device_id=str(device.device_id),
                        action_type=ActionType.turn_on,
                        triggered_by=TriggerType.rule_engine,
                        parameters=params,
                        reason=reason,
                    )
        return None

    def check_safety_constraints(self, device: DeviceState, action: ControlAction) -> bool:
        """Validate that an action does not violate device safety constraints."""

        safety = device.capabilities.get("safety") if device.capabilities else None
        if not safety:
            return True

        # Example safeties: min_temp, max_temp, max_duty_cycle
        min_temp = safety.get("min_temp")
        max_temp = safety.get("max_temp")
        target = action.parameters.get("temperature")
        if target is not None:
            if min_temp is not None and target < min_temp:
                logger.warning("Safety min temp violated", extra={"device": device.device_id})
                return False
            if max_temp is not None and target > max_temp:
                logger.warning("Safety max temp violated", extra={"device": device.device_id})
                return False

        max_duty = safety.get("max_duty_cycle_minutes")
        last_run = device.state.get("last_run_at") if device.state else None
        if max_duty and last_run:
            try:
                last_dt = datetime.fromisoformat(last_run)
            except Exception:
                last_dt = None
            if last_dt and datetime.now(UTC) - last_dt < timedelta(minutes=max_duty):
                logger.info("Device duty cycle limiter hit", extra={"device": device.device_id})
                return False
        return True

    def check_occupancy_transition(self, zone: ZoneState, occupied: bool) -> ControlAction | None:
        """Apply setback/boost when occupancy changes."""

        last_change = zone.last_occupancy_change
        if last_change and datetime.now(UTC) - last_change < timedelta(minutes=5):
            return None

        setback = zone.metrics.get("setback_c", 2.0)
        target_temp = zone.metrics.get("target_temperature_c")
        if target_temp is None or zone.temperature_c is None:
            return None

        desired = target_temp if occupied else target_temp - setback
        if abs(desired - zone.temperature_c) < 0.5:
            return None

        device = self._select_device(zone)
        if not device:
            return None

        return ControlAction(
            zone_id=str(zone.zone_id),
            device_id=str(device.device_id),
            action_type=ActionType.set_temperature,
            triggered_by=TriggerType.rule_engine,
            parameters={"temperature": desired},
            reason="occupancy transition",
        )

    def detect_anomaly(
        self, zone: ZoneState, readings: Iterable[dict[str, float | bool]]
    ) -> str | None:
        """Detect anomalies like sensor drift or device stuck states."""

        temps: list[float] = []
        for sample in readings:
            value = sample.get("temperature_c")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                temps.append(float(value))
        if len(temps) >= 3:
            avg = sum(temps) / len(temps)
            if zone.temperature_c is not None and abs(zone.temperature_c - avg) >= 3.5:
                return "sensor_drift"

        trend = zone.temp_trend_c_per_hour()
        if trend is not None and abs(trend) < 0.05 and zone.devices:
            active = any(device.state.get("is_running") for device in zone.devices.values())
            if active:
                return "device_unresponsive"

        humidity_trend = zone.humidity_trend_per_hour()
        if humidity_trend is not None and abs(humidity_trend) > 25:
            return "humidity_spike"
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_action(
        self, zone: ZoneState, *, colder: bool, reading_temp: float, target: float
    ) -> ControlAction | None:
        device = self._select_device(zone)
        if not device:
            return None

        direction = "heat" if colder else "cool"
        reason = (
            f"temperature {direction} needed (reading={reading_temp:.1f}C target={target:.1f}C)"
        )
        params: dict[str, float | int | str | bool] = {
            "temperature": target,
            "mode": direction,
        }
        return ControlAction(
            zone_id=str(zone.zone_id),
            device_id=str(device.device_id),
            action_type=ActionType.set_temperature,
            triggered_by=TriggerType.rule_engine,
            parameters=params,
            reason=reason,
        )

    def _select_device(
        self,
        zone: ZoneState,
        *,
        preferred: set[DeviceType] | None = None,
    ) -> DeviceState | None:
        devices = list(zone.devices.values())
        if not devices:
            return None
        if preferred:
            for device in devices:
                if isinstance(device.type, DeviceType) and device.type in preferred:
                    return device
        for device in devices:
            caps = device.capabilities or {}
            if caps.get("supports_temperature"):
                return device
        return devices[0]


__all__ = ["ControlAction", "RuleEngine"]
