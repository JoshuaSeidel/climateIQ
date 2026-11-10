"""Scenario harness: the real control loops against a simulated house.

* ``FakeHouseHA`` subclasses the real ``HAClient`` and only replaces the
  transport (``get_state`` / ``get_config`` / ``call_service``), so the real
  setpoint logic (dual setpoints, preset clearing, holds) runs unchanged.
  It simulates one whole-house thermostat (°F, Ecobee-like) and one
  temperature sensor per zone.
* ``House`` seeds the database (settings, zones, sensors, schedules, system
  mode), installs the fake HA client, freezes local time, and runs ticks of
  the real loops in ``backend.api.main``.

Spec: ``docs/CONTROL_SPEC.md``.  Requires PostgreSQL (see tests/conftest.py).
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import time_machine
from sqlalchemy import text

import backend.api.dependencies as deps
import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState, HAClient
from backend.models.database import (
    Device,
    Schedule,
    Sensor,
    SystemConfig,
    SystemSetting,
    Zone,
    get_session_maker,
)
from backend.models.enums import ControlMethod, DeviceType, SensorType, SystemMode, ZoneType

TZ = ZoneInfo("America/New_York")
THERMOSTAT = "climate.thermostat"


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


class FakeHouseHA(HAClient):
    """Simulated Home Assistant: one Ecobee-like thermostat (°F) + room sensors."""

    def __init__(self) -> None:
        super().__init__(url="http://fake-ha", token="fake")  # noqa: S106
        self.entities: dict[str, EntityState] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.set_thermostat(mode="heat", setpoint_f=69.0, reading_f=70.0)

    # ── simulation controls ──────────────────────────────────────────────
    def set_thermostat(
        self,
        *,
        mode: str | None = None,
        setpoint_f: float | None = None,
        reading_f: float | None = None,
        low_f: float | None = None,
        high_f: float | None = None,
        preset: str | None = None,
    ) -> None:
        st = self.entities.get(THERMOSTAT) or EntityState(
            entity_id=THERMOSTAT,
            state="heat",
            attributes={
                "hvac_modes": ["off", "heat", "cool", "heat_cool"],
                "current_temperature": 70.0,
                "temperature": 69.0,
                "preset_mode": "home",
                "preset_modes": ["home", "away", "sleep", "temp"],
                "fan_mode": "auto",
                "fan_modes": ["on", "auto"],
            },
        )
        attrs = st.attributes
        if mode is not None:
            st.state = mode
        if reading_f is not None:
            attrs["current_temperature"] = reading_f
        if st.state in ("heat_cool", "auto"):
            attrs.pop("temperature", None)
            if low_f is not None:
                attrs["target_temp_low"] = low_f
            if high_f is not None:
                attrs["target_temp_high"] = high_f
            attrs.setdefault("target_temp_low", 68.0)
            attrs.setdefault("target_temp_high", 76.0)
        else:
            attrs.pop("target_temp_low", None)
            attrs.pop("target_temp_high", None)
            if setpoint_f is not None:
                attrs["temperature"] = setpoint_f
            attrs.setdefault("temperature", 69.0)
        if preset is not None:
            attrs["preset_mode"] = preset
        self.entities[THERMOSTAT] = st
        self._recompute_action()

    def set_room(self, entity_id: str, temp_f: float) -> None:
        self.entities[entity_id] = EntityState(
            entity_id=entity_id,
            state=f"{temp_f:.1f}",
            attributes={"unit_of_measurement": "°F", "device_class": "temperature"},
        )

    def _recompute_action(self) -> None:
        st = self.entities[THERMOSTAT]
        a = st.attributes
        cur = float(a.get("current_temperature", 70.0))
        action = "idle"
        if st.state == "heat" and cur < float(a.get("temperature", 0)) - 0.5:
            action = "heating"
        elif st.state == "cool" and cur > float(a.get("temperature", 200)) + 0.5:
            action = "cooling"
        elif st.state in ("heat_cool", "auto"):
            if cur < float(a.get("target_temp_low", 0)) - 0.5:
                action = "heating"
            elif cur > float(a.get("target_temp_high", 200)) + 0.5:
                action = "cooling"
        elif st.state == "off":
            action = "off"
        a["hvac_action"] = action

    # ── observed thermostat state ────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self.entities[THERMOSTAT].state

    @property
    def setpoint_f(self) -> float | None:
        v = self.entities[THERMOSTAT].attributes.get("temperature")
        return None if v is None else float(v)

    @property
    def action(self) -> str:
        return str(self.entities[THERMOSTAT].attributes.get("hvac_action"))

    def would_heat(self) -> bool:
        """Heat is (or will be) running: heat mode with setpoint above the reading."""
        a = self.entities[THERMOSTAT].attributes
        if self.mode == "heat":
            return float(a["temperature"]) > float(a["current_temperature"])
        if self.mode in ("heat_cool", "auto"):
            return float(a["target_temp_low"]) > float(a["current_temperature"])
        return False

    def would_cool(self) -> bool:
        a = self.entities[THERMOSTAT].attributes
        if self.mode == "cool":
            return float(a["temperature"]) < float(a["current_temperature"])
        if self.mode in ("heat_cool", "auto"):
            return float(a["target_temp_high"]) < float(a["current_temperature"])
        return False

    def writes(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [c for c in self.calls if c[0] == "climate"]

    # ── HAClient transport overrides ─────────────────────────────────────
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_config(self) -> dict[str, Any]:
        return {
            "unit_system": {"temperature": "°F"},
            "time_zone": "America/New_York",
            "version": "fake",
            "location_name": "Sim",
        }

    async def get_state(self, entity_id: str) -> EntityState:
        from backend.integrations.ha_client import HANotFoundError

        st = self.entities.get(entity_id)
        if st is None:
            raise HANotFoundError(f"{entity_id} not found")
        return copy.deepcopy(st)

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
        *,
        return_response: bool = False,
    ) -> Any:
        payload = dict(data or {})
        if target:
            payload.update(target)
        self.calls.append((domain, service, payload))
        eid = payload.get("entity_id")
        if domain == "climate" and eid == THERMOSTAT:
            a = self.entities[THERMOSTAT].attributes
            if service == "set_temperature":
                if "temperature" in payload:
                    a["temperature"] = float(payload["temperature"])
                if "target_temp_low" in payload:
                    a["target_temp_low"] = float(payload["target_temp_low"])
                if "target_temp_high" in payload:
                    a["target_temp_high"] = float(payload["target_temp_high"])
                a["preset_mode"] = "temp"
            elif service == "set_hvac_mode":
                self.set_thermostat(mode=str(payload["hvac_mode"]))
            elif service == "set_preset_mode":
                a["preset_mode"] = payload.get("preset_mode")
            self._recompute_action()
        return [] if not return_response else {"changed_states": [], "service_response": {}}


@dataclass
class House:
    """A seeded, simulated house.  Use via the ``house`` fixture."""

    ha: FakeHouseHA = field(default_factory=FakeHouseHA)
    zones: dict[str, uuid.UUID] = field(default_factory=dict)
    schedules: dict[str, uuid.UUID] = field(default_factory=dict)
    _traveller: Any = None

    # ── setup ────────────────────────────────────────────────────────────
    async def reset(self, mode: SystemMode = SystemMode.scheduled) -> None:
        async with get_session_maker()() as db:
            tables = [
                "device_actions", "sensor_readings", "occupancy_patterns",
                "user_directives", "conversations", "user_feedback",
                "schedules", "devices", "sensors", "zones",
                "system_settings", "system_config",
            ]
            await db.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
            db.add(SystemConfig(id=1, current_mode=mode, llm_settings={}))
            for key, value in {
                "climate_entities": THERMOSTAT,
                "timezone": "America/New_York",
                "temperature_unit": "F",
                "ai_advisor_enabled": False,
                "hvac_control_mode": "auto",
                "mode_switch_cooldown_minutes": 30,
                "max_temp_offset_f": 8.0,
            }.items():
                db.add(SystemSetting(key=key, value={"value": value}))
            await db.commit()
        deps._ha_client = self.ha
        for name in (
            "_last_offset_temp", "_last_mode_switch", "_active_mode_state",
            "_active_mode_setpoint_history", "_active_mode_last_notify",
            "_fan_baseline", "_vent_last_position", "_zone_occupancy_stable",
        ):
            getattr(main_mod, name).clear()
        main_mod._last_executed_schedules = set()
        main_mod._notification_service = None
        main_mod.settings_instance.temperature_unit = "F"
        main_mod.settings_instance.climate_entities = ""
        from backend.core import mode_windows, seasonal_lock, user_tz
        from backend.core.climate_advisor import clear_advisor_cache

        clear_advisor_cache()
        mode_windows._escape_latched.clear()
        seasonal_lock._override_latched.clear()
        user_tz._last_ha_tz = None
        user_tz._last_resolved = None
        main_mod._preconditioning.clear()
        main_mod._runtime_state_loaded = False
        main_mod._mode_switch_loaded = False
        main_mod._gap_handed_back = False
        main_mod._gap_undo_tries = 0
        main_mod._follow_me_observed = False
        main_mod._fan_user_owned.clear()
        main_mod._ha_unit_cache = None
        main_mod._primary_climate_cache = None

    async def set_mode(self, mode: SystemMode) -> None:
        async with get_session_maker()() as db:
            cfg = await db.get(SystemConfig, 1)
            assert cfg is not None
            cfg.current_mode = mode
            await db.commit()

    async def set_setting(self, key: str, value: Any) -> None:
        async with get_session_maker()() as db:
            row = (
                await db.execute(
                    text("SELECT id FROM system_settings WHERE key = :k").bindparams(k=key)
                )
            ).first()
            if row:
                await db.execute(
                    text("UPDATE system_settings SET value = CAST(:v AS jsonb) WHERE key = :k")
                    .bindparams(v=__import__("json").dumps({"value": value}), k=key)
                )
            else:
                db.add(SystemSetting(key=key, value={"value": value}))
            await db.commit()

    async def add_zone(self, name: str, temp_f: float, **zone_kwargs: Any) -> uuid.UUID:
        entity = f"sensor.{name.lower().replace(' ', '_')}_temperature"
        async with get_session_maker()() as db:
            zone = Zone(name=name, type=ZoneType.bedroom, **zone_kwargs)
            db.add(zone)
            await db.flush()
            db.add(Sensor(
                zone_id=zone.id, name=f"{name} sensor", type=SensorType.multisensor,
                ha_entity_id=entity,
            ))
            await db.commit()
            self.zones[name] = zone.id
        self.ha.set_room(entity, temp_f)
        return self.zones[name]

    async def add_thermostat_device(self, zone_name: str) -> None:
        async with get_session_maker()() as db:
            db.add(Device(
                zone_id=self.zones[zone_name], name="Thermostat", type=DeviceType.thermostat,
                control_method=ControlMethod.ha_service_call, ha_entity_id=THERMOSTAT,
            ))
            await db.commit()

    def set_room(self, name: str, temp_f: float) -> None:
        self.ha.set_room(f"sensor.{name.lower().replace(' ', '_')}_temperature", temp_f)

    async def add_schedule(
        self,
        name: str,
        start: str,
        end: str | None,
        target_f: float,
        zones: list[str] | None = None,
        days: list[int] | None = None,
        priority: int = 5,
        hvac_mode: str = "auto",
    ) -> uuid.UUID:
        async with get_session_maker()() as db:
            sched = Schedule(
                name=name,
                start_time=start,
                end_time=end,
                target_temp_c=f_to_c(target_f),
                zone_ids=[str(self.zones[z]) for z in (zones or [])],
                days_of_week=days if days is not None else [0, 1, 2, 3, 4, 5, 6],
                priority=priority,
                hvac_mode=hvac_mode,
                is_enabled=True,
            )
            db.add(sched)
            await db.commit()
            self.schedules[name] = sched.id
        return self.schedules[name]

    # ── time ─────────────────────────────────────────────────────────────
    def at(self, y: int, mo: int, d: int, h: int, mi: int = 0) -> None:
        """Freeze local time (America/New_York)."""
        self.stop_clock()
        self._traveller = time_machine.travel(datetime(y, mo, d, h, mi, tzinfo=TZ), tick=False)
        self._traveller.start()

    def stop_clock(self) -> None:
        if self._traveller is not None:
            self._traveller.stop()
            self._traveller = None

    # ── run the real loops ───────────────────────────────────────────────
    async def tick_scheduled(self) -> None:
        """One Scheduled-mode tick: schedule runner then climate maintenance."""
        await main_mod.execute_schedules()
        await main_mod.maintain_climate_offset()

    async def tick_all(self) -> None:
        """Every thermostat-writing loop once (each must self-gate by mode)."""
        await main_mod.execute_schedules()
        await main_mod.maintain_climate_offset()
        await main_mod.execute_follow_me_mode()
        await main_mod.execute_active_mode()
