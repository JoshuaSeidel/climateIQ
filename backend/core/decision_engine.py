"""Decision engine orchestrating ClimateIQ control loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import SETTINGS
from backend.core.pattern_engine import PatternEngine
from backend.core.rule_engine import ControlAction, RuleEngine
from backend.core.scheduler import Scheduler
from backend.core.zone_manager import ZoneManager, ZoneState
from backend.integrations.ha_client import HAClient
from backend.integrations.llm.provider import ClimateIQLLMProvider, ProviderSettings
from backend.models import ActionType, DeviceAction, TriggerType
from backend.models.database import Device, SensorReading
from backend.models.enums import SystemMode

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DecisionResult:
    zone_id: UUID
    action: ControlAction | None
    used_llm: bool
    reason: str
    timestamp: datetime


class DecisionEngine:
    """Main orchestrator of ClimateIQ control logic."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        zone_manager: ZoneManager,
        scheduler: Scheduler,
        ha_client: HAClient | None = None,
        llm_provider: ClimateIQLLMProvider | None = None,
    ) -> None:
        self._session = session
        self._zone_manager = zone_manager
        self._scheduler = scheduler
        self._rule_engine = RuleEngine()
        self._pattern_engine = PatternEngine(session)
        self._ha = ha_client or HAClient(
            str(SETTINGS.home_assistant_url), SETTINGS.home_assistant_token
        )
        if llm_provider:
            self._llm = llm_provider
            self._llm_override = True
        else:
            self._llm = self._build_llm_provider()
            self._llm_override = False
        self._loop_task: asyncio.Task[None] | None = None

    def _build_llm_provider(self) -> ClimateIQLLMProvider:
        primary_provider = "anthropic"
        primary_model = "claude-sonnet-4-6"
        secondary_provider = "openai"
        secondary_model = "gpt-4o-mini"

        primary = ProviderSettings(
            provider=primary_provider,
            api_key=self._provider_api_key(primary_provider),
            default_model=primary_model,
        )
        secondary = ProviderSettings(
            provider=secondary_provider,
            api_key=self._provider_api_key(secondary_provider),
            default_model=secondary_model,
        )
        return ClimateIQLLMProvider(primary=primary, secondary=secondary)

    def _provider_api_key(self, provider: str) -> str:
        provider_key = provider.lower()
        if provider_key == "anthropic":
            return SETTINGS.anthropic_api_key
        if provider_key == "openai":
            return SETTINGS.openai_api_key
        if provider_key == "gemini":
            return SETTINGS.gemini_api_key
        if provider_key == "grok":
            return SETTINGS.grok_api_key
        if provider_key == "ollama":
            return ""
        if provider_key == "llamacpp":
            return ""
        return SETTINGS.openai_api_key

    async def run_control_loop(self) -> None:
        """Run the control loop every five minutes."""

        if self._loop_task and not self._loop_task.done():
            return

        async def _loop() -> None:
            consecutive_failures = 0
            while True:
                try:
                    await self._tick()
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.critical(
                            "Decision loop has failed %d consecutive times — "
                            "HVAC control may be degraded",
                            consecutive_failures,
                        )
                    else:
                        logger.exception("Decision loop tick failed")
                await asyncio.sleep(300)

        self._loop_task = asyncio.create_task(_loop(), name="climateiq-decision-loop")

    async def _tick(self) -> None:
        zones = await self.gather_state()
        actions = await self.analyze_zones(zones)
        for zone, action in actions:
            decision = await self.make_decision(zone, action)
            if decision.action:
                await self.execute_action(decision.action)
            await self.record_decision(decision)

    async def gather_state(self) -> list[ZoneState]:
        return self._zone_manager.zones_needing_attention()

    async def analyze_zones(
        self, zones: Iterable[ZoneState]
    ) -> list[tuple[ZoneState, ControlAction | None]]:
        results: list[tuple[ZoneState, ControlAction | None]] = []
        for zone in zones:
            reading: dict[str, float | bool] = {}
            if isinstance(zone.temperature_c, (int, float)) and not isinstance(
                zone.temperature_c, bool
            ):
                reading["temperature_c"] = float(zone.temperature_c)
            if isinstance(zone.humidity, (int, float)) and not isinstance(zone.humidity, bool):
                reading["humidity"] = float(zone.humidity)
            if isinstance(zone.occupancy, bool):
                reading["occupied"] = zone.occupancy
            action = self._rule_engine.check_comfort_band(zone, reading)
            if not action and zone.occupancy is not None:
                action = self._rule_engine.check_occupancy_transition(zone, zone.occupancy)
            results.append((zone, action))
        return results

    async def make_decision(
        self, zone: ZoneState, draft_action: ControlAction | None
    ) -> DecisionResult:
        if draft_action:
            return DecisionResult(
                zone_id=zone.zone_id,
                action=draft_action,
                used_llm=False,
                reason=draft_action.reason,
                timestamp=datetime.now(UTC),
            )

        system_mode = await self._fetch_system_mode()
        if system_mode == SystemMode.learn:
            await self._learn_from_zone(zone)

        llm_provider = await self._get_configured_llm_provider()
        prompt = f"Zone {zone.name} needs attention. Temp={zone.temperature_c}C target={zone.metrics.get('target_temperature_c')}"
        response = await asyncio.to_thread(
            llm_provider.chat,
            messages=[
                {"role": "system", "content": "You are ClimateIQ"},
                {"role": "user", "content": prompt},
            ],
        )
        action = self._translate_llm_response(zone, response)
        reason = "llm_decision"
        return DecisionResult(
            zone_id=zone.zone_id,
            action=action,
            used_llm=True,
            reason=reason,
            timestamp=datetime.now(UTC),
        )

    async def execute_action(self, action: ControlAction) -> None:
        device = (
            await self._session.get(Device, UUID(action.device_id)) if action.device_id else None
        )
        if not device:
            logger.warning("No device found for action", extra={"action": action})
            return
        entity_id = device.ha_entity_id or f"climate.{device.name.lower().replace(' ', '_')}"

        if action.action_type == ActionType.set_temperature:
            temp_value = action.parameters.get("temperature", SETTINGS.default_comfort_temp_min_c)
            if isinstance(temp_value, (int, float)) and not isinstance(temp_value, bool):
                temp = float(temp_value)
            else:
                temp = SETTINGS.default_comfort_temp_min_c
            # Safety clamp
            temp = max(SETTINGS.safety_min_temp_c, min(SETTINGS.safety_max_temp_c, temp))
            await self._ha.set_climate_temperature(entity_id, temp)
        elif action.action_type == ActionType.turn_on:
            await self._ha.turn_on(entity_id)
        elif action.action_type == ActionType.turn_off:
            await self._ha.turn_off(entity_id)

        record = DeviceAction(
            device_id=device.id,
            triggered_by=action.triggered_by,
            action_type=action.action_type,
            parameters=action.parameters,
            result={"source": action.reason},
        )
        self._session.add(record)
        await self._session.commit()

    async def record_decision(self, decision: DecisionResult) -> None:
        logger.info(
            "Decision: zone=%s action=%s llm=%s reason=%s",
            decision.zone_id,
            decision.action.action_type.value if decision.action else "none",
            decision.used_llm,
            decision.reason,
        )

    # ------------------------------------------------------------------
    async def _fetch_system_mode(self) -> SystemMode:
        """Fetch the current system mode from the database."""
        from backend.models.database import SystemConfig

        result = await self._session.execute(
            SystemConfig.__table__.select().where(SystemConfig.id == 1)
        )
        row = result.first()
        if row is None:
            logger.warning("No system config found, defaulting to 'learn' mode")
            return SystemMode.learn
        return SystemMode(row.current_mode)

    async def _get_configured_llm_provider(self) -> ClimateIQLLMProvider:
        if self._llm_override:
            return self._llm
        from backend.models.database import SystemConfig

        primary_provider = "anthropic"
        primary_model = "claude-sonnet-4-6"

        try:
            result = await self._session.execute(
                SystemConfig.__table__.select().where(SystemConfig.id == 1)
            )
            row = result.first()
            llm_settings = dict(row.llm_settings or {}) if row else {}
            primary_provider = llm_settings.get("provider", primary_provider)
            primary_model = llm_settings.get("model", primary_model)
        except Exception:
            logger.debug("Failed to load LLM settings; using defaults")

        primary = ProviderSettings(
            provider=primary_provider,
            api_key=self._provider_api_key(primary_provider),
            default_model=primary_model,
        )
        secondary = ProviderSettings(
            provider="openai",
            api_key=self._provider_api_key("openai"),
            default_model="gpt-4o-mini",
        )
        return ClimateIQLLMProvider(primary=primary, secondary=secondary)

    async def _learn_from_zone(self, zone: ZoneState) -> None:
        from backend.core.pattern_engine import OccupancyReading

        result = await self._session.execute(
            SensorReading.__table__.select().where(
                SensorReading.recorded_at >= datetime.now(UTC) - timedelta(days=7),
                SensorReading.zone_id == zone.zone_id,
            )
        )
        rows = result.fetchall()
        readings = [
            OccupancyReading(
                zone_id=str(row.zone_id),
                timestamp=row.recorded_at,
                occupied=row.presence,
            )
            for row in rows
            if row.presence is not None
        ]
        await self._pattern_engine.learn_occupancy_patterns(str(zone.zone_id), readings)

    def _translate_llm_response(
        self, zone: ZoneState, response: dict[str, object]
    ) -> ControlAction | None:
        try:
            content = response["choices"][0]["message"]["content"]  # type: ignore[index]
        except Exception:
            logger.warning("Failed to parse LLM response structure: %s", response)
            return None

        text = str(content).lower()
        device = next(iter(zone.devices.values()), None)
        if not device:
            logger.warning("No device in zone %s to act on", zone.name)
            return None

        device_id = str(device.device_id)
        zone_id = str(zone.zone_id)

        # Check for negation/stop patterns first
        negation_patterns = [
            "don't heat",
            "don't cool",
            "no need to heat",
            "no need to cool",
            "stop heating",
            "stop cooling",
            "turn off",
            "shut off",
            "shut down",
        ]
        for pattern in negation_patterns:
            if pattern in text:
                return ControlAction(
                    zone_id=zone_id,
                    device_id=device_id,
                    action_type=ActionType.turn_off,
                    triggered_by=TriggerType.llm_decision,
                    parameters={},
                    reason="llm_turn_off",
                )

        # Check for heating
        if any(kw in text for kw in ("heat", "warm", "raise temperature", "increase temperature")):
            target = zone.metrics.get("target_temperature_c", SETTINGS.default_comfort_temp_min_c)
            temperature = (
                float(target)
                if isinstance(target, (int, float)) and not isinstance(target, bool)
                else SETTINGS.default_comfort_temp_min_c
            )
            return ControlAction(
                zone_id=zone_id,
                device_id=device_id,
                action_type=ActionType.set_temperature,
                triggered_by=TriggerType.llm_decision,
                parameters={"temperature": temperature},
                reason="llm_heat",
            )

        # Check for cooling
        if any(
            kw in text
            for kw in ("cool", "lower temperature", "decrease temperature", "reduce temperature")
        ):
            target = zone.metrics.get("target_temperature_c", SETTINGS.default_comfort_temp_max_c)
            temperature = (
                float(target)
                if isinstance(target, (int, float)) and not isinstance(target, bool)
                else SETTINGS.default_comfort_temp_max_c
            )
            return ControlAction(
                zone_id=zone_id,
                device_id=device_id,
                action_type=ActionType.set_temperature,
                triggered_by=TriggerType.llm_decision,
                parameters={"temperature": temperature},
                reason="llm_cool",
            )

        # Check for turn on
        if "turn on" in text:
            return ControlAction(
                zone_id=zone_id,
                device_id=device_id,
                action_type=ActionType.turn_on,
                triggered_by=TriggerType.llm_decision,
                parameters={},
                reason="llm_turn_on",
            )

        logger.info("LLM response did not contain a recognized action: %s", text[:200])
        return None


__all__ = ["DecisionEngine", "DecisionResult"]
