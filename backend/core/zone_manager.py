"""Zone state management utilities for ClimateIQ."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import fmean
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import Device, DeviceType, SensorReading, Zone

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class DeviceState:
    """Snapshot of a controllable device and its last-known attributes."""

    device_id: UUID
    name: str
    type: DeviceType | str
    control_method: str
    capabilities: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)
    last_updated: datetime = field(default_factory=_utc_now)

    def update(
        self, payload: MutableMapping[str, Any], *, timestamp: datetime | None = None
    ) -> None:
        self.state.update(payload)
        self.last_updated = timestamp or _utc_now()


@dataclass(slots=True)
class ZoneState:
    """Aggregated state for a ClimateIQ zone."""

    zone_id: UUID
    name: str
    floor: int | None = None
    zone_type: str | None = None
    is_active: bool = True
    temperature_c: float | None = None
    humidity: float | None = None
    occupancy: bool | None = None
    comfort_score: float = 0.0
    last_sensor_update: datetime = field(default_factory=_utc_now)
    last_occupancy_change: datetime = field(default_factory=_utc_now)
    devices: dict[UUID, DeviceState] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    attention_flags: set[str] = field(default_factory=set)
    _temp_history: deque[tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=288))
    _humidity_history: deque[tuple[datetime, float]] = field(
        default_factory=lambda: deque(maxlen=288)
    )

    def register_device(self, device: Device) -> None:
        self.devices[device.id] = DeviceState(
            device_id=device.id,
            name=device.name,
            type=device.type,
            control_method=device.control_method.value
            if hasattr(device.control_method, "value")
            else str(device.control_method),
            capabilities=dict(device.capabilities or {}),
            state={},
        )

    def record_temperature(
        self,
        value: float,
        *,
        timestamp: datetime | None = None,
        alpha: float = 0.3,
    ) -> None:
        timestamp = timestamp or _utc_now()
        if self.temperature_c is None:
            self.temperature_c = value
        else:
            self.temperature_c = (alpha * value) + (1 - alpha) * self.temperature_c
        self._temp_history.append((timestamp, self.temperature_c))
        self.last_sensor_update = timestamp

    def record_humidity(
        self,
        value: float,
        *,
        timestamp: datetime | None = None,
        alpha: float = 0.3,
    ) -> None:
        timestamp = timestamp or _utc_now()
        if self.humidity is None:
            self.humidity = value
        else:
            self.humidity = (alpha * value) + (1 - alpha) * self.humidity
        self._humidity_history.append((timestamp, self.humidity))
        self.last_sensor_update = timestamp

    def record_occupancy(self, occupied: bool, *, timestamp: datetime | None = None) -> None:
        timestamp = timestamp or _utc_now()
        if self.occupancy is None or self.occupancy != occupied:
            self.last_occupancy_change = timestamp
        self.occupancy = occupied
        self.last_sensor_update = timestamp

    def temp_trend_c_per_hour(self, *, lookback_minutes: int = 90) -> float | None:
        cutoff = _utc_now() - timedelta(minutes=lookback_minutes)
        samples = [sample for sample in self._temp_history if sample[0] >= cutoff]
        if len(samples) < 2:
            return None
        (start_ts, start_temp), (end_ts, end_temp) = samples[0], samples[-1]
        dt_minutes = (end_ts - start_ts).total_seconds() / 60.0
        if dt_minutes <= 0:
            return None
        return (end_temp - start_temp) / dt_minutes * 60.0

    def humidity_trend_per_hour(self, *, lookback_minutes: int = 90) -> float | None:
        cutoff = _utc_now() - timedelta(minutes=lookback_minutes)
        samples = [sample for sample in self._humidity_history if sample[0] >= cutoff]
        if len(samples) < 2:
            return None
        (start_ts, start_h), (end_ts, end_h) = samples[0], samples[-1]
        dt_minutes = (end_ts - start_ts).total_seconds() / 60.0
        if dt_minutes <= 0:
            return None
        return (end_h - start_h) / dt_minutes * 60.0

    def set_metric(self, key: str, value: float) -> None:
        self.metrics[key] = value

    def push_flag(self, flag: str, *, active: bool) -> None:
        if active:
            self.attention_flags.add(flag)
        else:
            self.attention_flags.discard(flag)


class ZoneManager:
    """Maintain live zone state derived from database + streaming sensors."""

    def __init__(self, *, smoothing_alpha: float = 0.3) -> None:
        self._zones: dict[UUID, ZoneState] = {}
        self._lock = asyncio.Lock()
        self._alpha = max(0.05, min(1.0, smoothing_alpha))

    def get_state(self, zone_id: UUID) -> ZoneState | None:
        return self._zones.get(zone_id)

    def iter_states(self) -> Iterable[ZoneState]:
        return list(self._zones.values())

    async def refresh_from_db(
        self, session: AsyncSession, *, include_inactive: bool = False
    ) -> None:
        stmt = select(Zone).options(
            selectinload(Zone.devices),
            selectinload(Zone.sensors),
        )
        if not include_inactive:
            stmt = stmt.where(Zone.is_active.is_(True))

        result = await session.execute(stmt)
        zones = list(result.scalars())
        async with self._lock:
            active_ids = {zone.id for zone in zones}
            stale = set(self._zones) - active_ids
            for zone_id in stale:
                self._zones.pop(zone_id, None)

            sensor_zone_map: dict[UUID, UUID] = {}
            for zone in zones:
                state = self._zones.get(zone.id)
                if not state:
                    state = ZoneState(
                        zone_id=zone.id,
                        name=zone.name,
                        floor=zone.floor,
                        zone_type=zone.type.value
                        if hasattr(zone.type, "value")
                        else str(zone.type),
                        is_active=zone.is_active,
                    )
                    self._zones[zone.id] = state
                else:
                    state.name = zone.name
                    state.floor = zone.floor
                    state.zone_type = (
                        zone.type.value if hasattr(zone.type, "value") else str(zone.type)
                    )
                    state.is_active = zone.is_active
                for device in zone.devices:
                    state.register_device(device)
                for sensor in zone.sensors:
                    sensor_zone_map[sensor.id] = zone.id

            await self._hydrate_latest_readings(session, sensor_zone_map)

    async def _hydrate_latest_readings(
        self, session: AsyncSession, sensor_zone_map: Mapping[UUID, UUID]
    ) -> None:
        if not sensor_zone_map:
            return

        sensor_ids = tuple(sensor_zone_map.keys())
        subquery = (
            select(
                SensorReading.sensor_id,
                func.max(SensorReading.recorded_at).label("recorded_at"),
            )
            .where(SensorReading.sensor_id.in_(sensor_ids))
            .group_by(SensorReading.sensor_id)
            .subquery()
        )
        stmt = (
            select(
                SensorReading.sensor_id,
                SensorReading.recorded_at,
                SensorReading.temperature_c,
                SensorReading.humidity,
                SensorReading.presence,
                SensorReading.payload,
            )
            .join(
                subquery,
                (SensorReading.sensor_id == subquery.c.sensor_id)
                & (SensorReading.recorded_at == subquery.c.recorded_at),
            )
            .select_from(SensorReading)
        )
        result = await session.execute(stmt)
        rows = result.all()
        for row in rows:
            zone_id = sensor_zone_map.get(row.sensor_id)
            if zone_id is None:
                continue
            state = self._zones.get(zone_id)
            if not state:
                continue
            timestamp = row.recorded_at
            if row.temperature_c is not None:
                state.record_temperature(row.temperature_c, timestamp=timestamp, alpha=self._alpha)
            if row.humidity is not None:
                state.record_humidity(row.humidity, timestamp=timestamp, alpha=self._alpha)
            if row.presence is not None:
                state.record_occupancy(row.presence, timestamp=timestamp)
            payload = row.payload or {}
            for key, value in payload.items():
                if isinstance(value, (int, float)):
                    state.set_metric(key, float(value))

    async def update_from_sensor_payload(
        self,
        *,
        zone_id: UUID,
        zone_name: str,
        temperature_c: float | None = None,
        humidity: float | None = None,
        occupancy: bool | None = None,
        metrics: MutableMapping[str, float] | None = None,
        timestamp: datetime | None = None,
    ) -> ZoneState:
        async with self._lock:
            state = self._zones.setdefault(zone_id, ZoneState(zone_id=zone_id, name=zone_name))
            if temperature_c is not None:
                state.record_temperature(temperature_c, timestamp=timestamp, alpha=self._alpha)
            if humidity is not None:
                state.record_humidity(humidity, timestamp=timestamp, alpha=self._alpha)
            if occupancy is not None:
                state.record_occupancy(occupancy, timestamp=timestamp)
            if metrics:
                for key, value in metrics.items():
                    state.set_metric(key, float(value))
            state.comfort_score = self._calculate_comfort_score(state)
            return state

    async def update_device_state(
        self,
        *,
        zone_id: UUID,
        device_id: UUID,
        device_name: str,
        device_type: DeviceType | str,
        control_method: str,
        capabilities: Mapping[str, Any] | None = None,
        state_payload: MutableMapping[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> DeviceState:
        async with self._lock:
            zone_state = self._zones.setdefault(
                zone_id, ZoneState(zone_id=zone_id, name=str(zone_id))
            )
            device_state = zone_state.devices.get(device_id)
            if not device_state:
                device_state = DeviceState(
                    device_id=device_id,
                    name=device_name,
                    type=device_type,
                    control_method=control_method,
                    capabilities=dict(capabilities or {}),
                )
                zone_state.devices[device_id] = device_state
            if state_payload:
                device_state.update(state_payload, timestamp=timestamp)
            return device_state

    async def snapshot(self) -> list[ZoneState]:
        async with self._lock:
            return [self._clone_state(state) for state in self._zones.values()]

    def zones_needing_attention(
        self,
        *,
        temperature_delta: float = 2.0,
        humidity_delta: float = 12.0,
        stale_minutes: int = 20,
    ) -> list[ZoneState]:
        now = _utc_now()
        flagged: list[ZoneState] = []
        for state in self._zones.values():
            needs_attention = False
            target_temp = state.metrics.get("target_temperature_c")
            target_humidity = state.metrics.get("target_humidity")
            if state.temperature_c is not None and target_temp is not None:
                if abs(state.temperature_c - target_temp) >= temperature_delta:
                    needs_attention = True
                    state.push_flag("temperature", active=True)
            if state.humidity is not None and target_humidity is not None:
                if abs(state.humidity - target_humidity) >= humidity_delta:
                    needs_attention = True
                    state.push_flag("humidity", active=True)
            if (now - state.last_sensor_update) >= timedelta(minutes=stale_minutes):
                needs_attention = True
                state.push_flag("stale", active=True)
            if needs_attention:
                flagged.append(state)
        return flagged

    def _calculate_comfort_score(self, state: ZoneState) -> float:
        components: list[float] = []
        target_temp = state.metrics.get("target_temperature_c")
        target_humidity = state.metrics.get("target_humidity")
        if target_temp is not None and state.temperature_c is not None:
            delta = abs(state.temperature_c - target_temp)
            components.append(max(0.0, 1.0 - delta / 5.0))
        if target_humidity is not None and state.humidity is not None:
            delta = abs(state.humidity - target_humidity)
            components.append(max(0.0, 1.0 - delta / 20.0))
        if state.occupancy is not None:
            components.append(1.0 if state.occupancy else 0.85)
        if not components:
            return 0.0
        return round(fmean(components) * 100.0, 1)

    def _clone_state(self, state: ZoneState) -> ZoneState:
        clone = ZoneState(
            zone_id=state.zone_id,
            name=state.name,
            floor=state.floor,
            zone_type=state.zone_type,
            is_active=state.is_active,
            temperature_c=state.temperature_c,
            humidity=state.humidity,
            occupancy=state.occupancy,
            comfort_score=state.comfort_score,
            last_sensor_update=state.last_sensor_update,
            last_occupancy_change=state.last_occupancy_change,
            devices={
                device_id: DeviceState(
                    device_id=device_id,
                    name=device.name,
                    type=device.type,
                    control_method=device.control_method,
                    capabilities=dict(device.capabilities),
                    state=dict(device.state),
                    last_updated=device.last_updated,
                )
                for device_id, device in state.devices.items()
            },
            metrics=dict(state.metrics),
            attention_flags=set(state.attention_flags),
        )
        clone._temp_history = deque(state._temp_history, maxlen=state._temp_history.maxlen)
        clone._humidity_history = deque(
            state._humidity_history, maxlen=state._humidity_history.maxlen
        )
        return clone


__all__ = ["DeviceState", "ZoneManager", "ZoneState"]
