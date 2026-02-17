"""Comprehensive unit tests for backend.core.decision_engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from backend.config import SETTINGS
from backend.core.decision_engine import DecisionEngine, DecisionResult
from backend.core.rule_engine import ControlAction
from backend.core.zone_manager import DeviceState, ZoneState
from backend.models.enums import ActionType, DeviceType, SystemMode, TriggerType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zone(
    zone_id: UUID | None = None,
    name: str = "TestZone",
    temperature_c: float = 22.0,
    humidity: float = 50.0,
    occupancy: bool | None = True,
    target_temp: float | None = 22.0,
) -> ZoneState:
    zs = ZoneState(zone_id=zone_id or uuid4(), name=name)
    zs.temperature_c = temperature_c
    zs.humidity = humidity
    zs.occupancy = occupancy
    if target_temp is not None:
        zs.metrics["target_temperature_c"] = target_temp
    return zs


def _make_zone_with_device(
    zone_id: UUID | None = None, device_id: UUID | None = None, **kwargs: Any
) -> ZoneState:
    zs = _make_zone(zone_id=zone_id, **kwargs)
    did = device_id or uuid4()
    zs.devices[did] = DeviceState(
        device_id=did,
        name="Thermostat",
        type=DeviceType.thermostat,
        control_method="ha_service_call",
        capabilities={"supports_temperature": True},
    )
    return zs


def _make_action(
    zone_id: UUID | None = None,
    device_id: UUID | None = None,
    action_type: ActionType = ActionType.set_temperature,
    triggered_by: TriggerType = TriggerType.rule_engine,
    parameters: dict[str, Any] | None = None,
    reason: str = "test action",
) -> ControlAction:
    return ControlAction(
        zone_id=str(zone_id or uuid4()),
        device_id=str(device_id or uuid4()),
        action_type=action_type,
        triggered_by=triggered_by,
        parameters=parameters if parameters is not None else {"temperature": 22.0},
        reason=reason,
    )


def _build_engine(
    session: Any = None,
    zone_manager: Any = None,
    scheduler: Any = None,
    ha_client: Any = None,
    llm_provider: Any = None,
) -> DecisionEngine:
    """Build a DecisionEngine with all dependencies mocked."""
    _session = session or AsyncMock()
    _zone_manager = zone_manager or MagicMock()
    _scheduler = scheduler or MagicMock()
    _ha_client = ha_client or AsyncMock()
    _llm_provider = llm_provider or MagicMock()

    with (
        patch("backend.core.decision_engine.RuleEngine") as mock_re_cls,
        patch("backend.core.decision_engine.PatternEngine"),
    ):
        mock_re_cls.return_value = MagicMock()
        engine = DecisionEngine(
            session=_session,
            zone_manager=_zone_manager,
            scheduler=_scheduler,
            ha_client=_ha_client,
            llm_provider=_llm_provider,
        )
    return engine


# ===================================================================
# gather_state
# ===================================================================


class TestGatherState:
    async def test_calls_zones_needing_attention(self) -> None:
        zm = MagicMock()
        zone = _make_zone()
        zm.zones_needing_attention.return_value = [zone]
        engine = _build_engine(zone_manager=zm)

        result = await engine.gather_state()

        zm.zones_needing_attention.assert_called_once()
        assert result == [zone]

    async def test_returns_empty_when_no_zones_need_attention(self) -> None:
        zm = MagicMock()
        zm.zones_needing_attention.return_value = []
        engine = _build_engine(zone_manager=zm)

        result = await engine.gather_state()
        assert result == []


# ===================================================================
# analyze_zones
# ===================================================================


class TestAnalyzeZones:
    async def test_calls_check_comfort_band(self) -> None:
        engine = _build_engine()
        zone = _make_zone(temperature_c=25.0, humidity=60.0, occupancy=True)
        action = _make_action()
        engine._rule_engine.check_comfort_band.return_value = action  # type: ignore[attr-defined]

        results = await engine.analyze_zones([zone])

        assert len(results) == 1
        assert results[0] == (zone, action)
        engine._rule_engine.check_comfort_band.assert_called_once()  # type: ignore[attr-defined]

    async def test_falls_through_to_occupancy_check_when_no_comfort_action(self) -> None:
        engine = _build_engine()
        zone = _make_zone(temperature_c=22.0, occupancy=True)
        occ_action = _make_action(reason="occupancy transition")
        engine._rule_engine.check_comfort_band.return_value = None  # type: ignore[attr-defined]
        engine._rule_engine.check_occupancy_transition.return_value = occ_action  # type: ignore[attr-defined]

        results = await engine.analyze_zones([zone])

        assert len(results) == 1
        assert results[0][1] == occ_action
        engine._rule_engine.check_occupancy_transition.assert_called_once_with(zone, True)  # type: ignore[attr-defined]

    async def test_no_occupancy_check_when_occupancy_is_none(self) -> None:
        engine = _build_engine()
        zone = _make_zone(occupancy=None)
        zone.occupancy = None
        engine._rule_engine.check_comfort_band.return_value = None  # type: ignore[attr-defined]

        results = await engine.analyze_zones([zone])

        assert results[0][1] is None
        engine._rule_engine.check_occupancy_transition.assert_not_called()  # type: ignore[attr-defined]

    async def test_reading_dict_built_correctly(self) -> None:
        engine = _build_engine()
        zone = _make_zone(temperature_c=23.5, humidity=55.0, occupancy=False)
        engine._rule_engine.check_comfort_band.return_value = None  # type: ignore[attr-defined]

        await engine.analyze_zones([zone])

        call_args = engine._rule_engine.check_comfort_band.call_args  # type: ignore[attr-defined]
        reading = call_args[0][1]
        assert reading["temperature_c"] == 23.5
        assert reading["humidity"] == 55.0
        assert reading["occupied"] is False

    async def test_multiple_zones(self) -> None:
        engine = _build_engine()
        z1 = _make_zone(name="Z1", temperature_c=20.0)
        z2 = _make_zone(name="Z2", temperature_c=25.0)
        engine._rule_engine.check_comfort_band.return_value = None  # type: ignore[attr-defined]
        engine._rule_engine.check_occupancy_transition.return_value = None  # type: ignore[attr-defined]

        results = await engine.analyze_zones([z1, z2])
        assert len(results) == 2


# ===================================================================
# make_decision
# ===================================================================


class TestMakeDecision:
    async def test_with_draft_action_returns_immediately(self) -> None:
        engine = _build_engine()
        zone = _make_zone()
        action = _make_action(reason="comfort band")

        result = await engine.make_decision(zone, action)

        assert isinstance(result, DecisionResult)
        assert result.action == action
        assert result.used_llm is False
        assert result.reason == "comfort band"
        assert result.zone_id == zone.zone_id

    async def test_without_draft_action_falls_through_to_llm(self) -> None:
        session = AsyncMock()
        llm = MagicMock()
        llm.chat.return_value = {"choices": [{"message": {"content": "No action needed"}}]}
        engine = _build_engine(session=session, llm_provider=llm)

        zone = _make_zone_with_device()

        # Mock _fetch_system_mode to return active (not learn)
        with patch.object(engine, "_fetch_system_mode", return_value=SystemMode.active):
            result = await engine.make_decision(zone, None)

        assert result.used_llm is True
        assert result.reason == "llm_decision"
        llm.chat.assert_called_once()

    async def test_llm_response_with_heat_keyword_creates_action(self) -> None:
        llm = MagicMock()
        llm.chat.return_value = {
            "choices": [{"message": {"content": "You should heat the zone to target"}}]
        }
        engine = _build_engine(llm_provider=llm)
        zone = _make_zone_with_device(target_temp=23.0)

        with patch.object(engine, "_fetch_system_mode", return_value=SystemMode.active):
            result = await engine.make_decision(zone, None)

        assert result.action is not None
        assert result.action.action_type == ActionType.set_temperature
        assert result.action.triggered_by == TriggerType.llm_decision
        assert result.action.parameters["temperature"] == 23.0

    async def test_llm_response_without_heat_returns_no_action(self) -> None:
        llm = MagicMock()
        llm.chat.return_value = {"choices": [{"message": {"content": "Everything looks fine"}}]}
        engine = _build_engine(llm_provider=llm)
        zone = _make_zone_with_device()

        with patch.object(engine, "_fetch_system_mode", return_value=SystemMode.active):
            result = await engine.make_decision(zone, None)

        assert result.action is None
        assert result.used_llm is True

    async def test_learn_mode_triggers_learn_from_zone(self) -> None:
        llm = MagicMock()
        llm.chat.return_value = {"choices": [{"message": {"content": "ok"}}]}
        engine = _build_engine(llm_provider=llm)
        zone = _make_zone_with_device()

        with (
            patch.object(engine, "_fetch_system_mode", return_value=SystemMode.learn),
            patch.object(engine, "_learn_from_zone", new_callable=AsyncMock) as mock_learn,
        ):
            await engine.make_decision(zone, None)
            mock_learn.assert_called_once_with(zone)


# ===================================================================
# execute_action
# ===================================================================


class TestExecuteAction:
    async def test_set_temperature_calls_ha_client(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Living Room Thermostat"
        mock_device.ha_entity_id = "climate.living_room"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.set_temperature,
            parameters={"temperature": 23.0},
        )

        await engine.execute_action(action)

        ha.set_climate_temperature.assert_awaited_once_with("climate.living_room", 23.0)
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    async def test_turn_on_calls_ha_client(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Heater"
        mock_device.ha_entity_id = "climate.heater"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.turn_on,
            parameters={},
        )

        await engine.execute_action(action)
        ha.turn_on.assert_awaited_once_with("climate.heater")

    async def test_turn_off_calls_ha_client(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Heater"
        mock_device.ha_entity_id = "climate.heater"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.turn_off,
            parameters={},
        )

        await engine.execute_action(action)
        ha.turn_off.assert_awaited_once_with("climate.heater")

    async def test_safety_clamp_low(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Thermostat"
        mock_device.ha_entity_id = "climate.thermostat"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.set_temperature,
            parameters={"temperature": 0.0},  # Below 4.4°C
        )

        await engine.execute_action(action)
        ha.set_climate_temperature.assert_awaited_once_with("climate.thermostat", 4.4)

    async def test_safety_clamp_high(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Thermostat"
        mock_device.ha_entity_id = "climate.thermostat"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.set_temperature,
            parameters={"temperature": 50.0},  # Above 37.8°C
        )

        await engine.execute_action(action)
        ha.set_climate_temperature.assert_awaited_once_with("climate.thermostat", 37.8)

    async def test_no_device_found_logs_warning_and_returns(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        session.get.return_value = None

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(device_id=uuid4())

        await engine.execute_action(action)

        ha.set_climate_temperature.assert_not_awaited()
        ha.turn_on.assert_not_awaited()
        ha.turn_off.assert_not_awaited()
        session.add.assert_not_called()

    async def test_records_device_action_in_db(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Thermostat"
        mock_device.ha_entity_id = "climate.thermostat"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.set_temperature,
            parameters={"temperature": 22.0},
            reason="comfort band",
        )

        await engine.execute_action(action)

        session.add.assert_called_once()
        record = session.add.call_args[0][0]
        assert record.device_id == device_id
        assert record.action_type == ActionType.set_temperature
        assert record.triggered_by == TriggerType.rule_engine
        assert record.parameters == {"temperature": 22.0}
        assert record.result == {"source": "comfort band"}

    async def test_temperature_defaults_to_comfort_min_when_invalid(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Thermostat"
        mock_device.ha_entity_id = "climate.thermostat"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.set_temperature,
            parameters={"temperature": "invalid"},
        )

        await engine.execute_action(action)
        ha.set_climate_temperature.assert_awaited_once_with(
            "climate.thermostat", SETTINGS.default_comfort_temp_min_c
        )

    async def test_temperature_defaults_to_comfort_min_when_missing(self) -> None:
        session = AsyncMock()
        ha = AsyncMock()
        device_id = uuid4()
        mock_device = MagicMock()
        mock_device.id = device_id
        mock_device.name = "Thermostat"
        mock_device.ha_entity_id = "climate.thermostat"
        session.get.return_value = mock_device

        engine = _build_engine(session=session, ha_client=ha)
        action = _make_action(
            device_id=device_id,
            action_type=ActionType.set_temperature,
            parameters={},
        )

        await engine.execute_action(action)
        ha.set_climate_temperature.assert_awaited_once_with(
            "climate.thermostat", SETTINGS.default_comfort_temp_min_c
        )


# ===================================================================
# record_decision
# ===================================================================


class TestRecordDecision:
    async def test_logs_decision(self) -> None:
        engine = _build_engine()
        zone_id = uuid4()
        action = _make_action(zone_id=zone_id)
        decision = DecisionResult(
            zone_id=zone_id,
            action=action,
            used_llm=False,
            reason="test",
            timestamp=datetime.now(UTC),
        )

        # Should not raise
        await engine.record_decision(decision)

    async def test_logs_decision_without_action(self) -> None:
        engine = _build_engine()
        zone_id = uuid4()
        decision = DecisionResult(
            zone_id=zone_id,
            action=None,
            used_llm=True,
            reason="no action needed",
            timestamp=datetime.now(UTC),
        )

        await engine.record_decision(decision)


# ===================================================================
# _fetch_system_mode
# ===================================================================


class TestFetchSystemMode:
    async def test_returns_mode_from_db(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.current_mode = "active"
        mock_result.first.return_value = mock_row
        session.execute.return_value = mock_result

        engine = _build_engine(session=session)
        mode = await engine._fetch_system_mode()

        assert mode == SystemMode.active

    async def test_defaults_to_learn_when_no_config(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        session.execute.return_value = mock_result

        engine = _build_engine(session=session)
        mode = await engine._fetch_system_mode()

        assert mode == SystemMode.learn

    async def test_returns_scheduled_mode(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.current_mode = "scheduled"
        mock_result.first.return_value = mock_row
        session.execute.return_value = mock_result

        engine = _build_engine(session=session)
        mode = await engine._fetch_system_mode()

        assert mode == SystemMode.scheduled


# ===================================================================
# _translate_llm_response
# ===================================================================


class TestTranslateLLMResponse:
    def test_heat_keyword_creates_set_temperature_action(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device(target_temp=23.0)
        response: dict[str, object] = {
            "choices": [{"message": {"content": "You should heat the room"}}]
        }

        action = engine._translate_llm_response(zone, response)

        assert action is not None
        assert action.action_type == ActionType.set_temperature
        assert action.triggered_by == TriggerType.llm_decision
        assert action.parameters["temperature"] == 23.0
        assert action.reason == "llm_heat"

    def test_heat_keyword_case_insensitive(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device(target_temp=21.0)
        response: dict[str, object] = {"choices": [{"message": {"content": "HEAT is recommended"}}]}

        action = engine._translate_llm_response(zone, response)
        assert action is not None

    def test_no_heat_keyword_returns_none(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device()
        response: dict[str, object] = {"choices": [{"message": {"content": "Everything is fine"}}]}

        action = engine._translate_llm_response(zone, response)
        assert action is None

    def test_no_device_returns_none_even_with_heat(self) -> None:
        engine = _build_engine()
        zone = _make_zone()  # No devices
        response: dict[str, object] = {"choices": [{"message": {"content": "heat needed"}}]}

        action = engine._translate_llm_response(zone, response)
        assert action is None

    def test_malformed_response_returns_none(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device()
        response: dict[str, object] = {"bad": "format"}

        action = engine._translate_llm_response(zone, response)
        assert action is None

    def test_empty_choices_returns_none(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device()
        response: dict[str, object] = {"choices": []}

        action = engine._translate_llm_response(zone, response)
        assert action is None

    def test_uses_target_temp_from_metrics(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device(target_temp=25.5)
        response: dict[str, object] = {"choices": [{"message": {"content": "heat it up"}}]}

        action = engine._translate_llm_response(zone, response)
        assert action is not None
        assert action.parameters["temperature"] == 25.5

    def test_defaults_to_comfort_min_when_no_target(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device(target_temp=None)
        response: dict[str, object] = {"choices": [{"message": {"content": "heat it up"}}]}

        action = engine._translate_llm_response(zone, response)
        assert action is not None
        assert action.parameters["temperature"] == SETTINGS.default_comfort_temp_min_c

    def test_zone_id_and_device_id_are_strings(self) -> None:
        engine = _build_engine()
        zone = _make_zone_with_device()
        response: dict[str, object] = {"choices": [{"message": {"content": "heat"}}]}

        action = engine._translate_llm_response(zone, response)
        assert action is not None
        assert isinstance(action.zone_id, str)
        assert isinstance(action.device_id, str)


# ===================================================================
# Integration-style: _tick flow
# ===================================================================


class TestTickFlow:
    async def test_tick_orchestrates_full_loop(self) -> None:
        zone = _make_zone_with_device()
        action = _make_action()

        zm = MagicMock()
        zm.zones_needing_attention.return_value = [zone]

        engine = _build_engine(zone_manager=zm)

        with (
            patch.object(engine, "gather_state", new_callable=AsyncMock, return_value=[zone]),
            patch.object(
                engine, "analyze_zones", new_callable=AsyncMock, return_value=[(zone, action)]
            ),
            patch.object(
                engine,
                "make_decision",
                new_callable=AsyncMock,
                return_value=DecisionResult(
                    zone_id=zone.zone_id,
                    action=action,
                    used_llm=False,
                    reason="test",
                    timestamp=datetime.now(UTC),
                ),
            ),
            patch.object(engine, "execute_action", new_callable=AsyncMock) as mock_exec,
            patch.object(engine, "record_decision", new_callable=AsyncMock) as mock_record,
        ):
            await engine._tick()

            mock_exec.assert_awaited_once_with(action)
            mock_record.assert_awaited_once()

    async def test_tick_skips_execute_when_no_action(self) -> None:
        zone = _make_zone()

        engine = _build_engine()

        decision_no_action = DecisionResult(
            zone_id=zone.zone_id,
            action=None,
            used_llm=True,
            reason="no action",
            timestamp=datetime.now(UTC),
        )

        with (
            patch.object(engine, "gather_state", new_callable=AsyncMock, return_value=[zone]),
            patch.object(
                engine, "analyze_zones", new_callable=AsyncMock, return_value=[(zone, None)]
            ),
            patch.object(
                engine, "make_decision", new_callable=AsyncMock, return_value=decision_no_action
            ),
            patch.object(engine, "execute_action", new_callable=AsyncMock) as mock_exec,
            patch.object(engine, "record_decision", new_callable=AsyncMock) as mock_record,
        ):
            await engine._tick()

            mock_exec.assert_not_awaited()
            mock_record.assert_awaited_once()
