"""Tests for backend.services.notification_service — HA notifications, history."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.integrations.ha_client import HAClient, HAClientError
from backend.services.notification_service import (
    NotificationService,
    _humanize_anomaly_type,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ha_client() -> AsyncMock:
    return AsyncMock(spec=HAClient)


@pytest.fixture()
def service(ha_client: AsyncMock) -> NotificationService:
    return NotificationService(ha_client)


@pytest.fixture()
def small_history_service(ha_client: AsyncMock) -> NotificationService:
    return NotificationService(ha_client, history_limit=3)


# ===================================================================
# _humanize_anomaly_type
# ===================================================================


class TestHumanizeAnomalyType:
    """Tests for the snake_case → Title Case converter."""

    def test_temperature_spike(self) -> None:
        assert _humanize_anomaly_type("temperature_spike") == "Temperature Spike"

    def test_sensor_offline(self) -> None:
        assert _humanize_anomaly_type("sensor_offline") == "Sensor Offline"

    def test_single_word(self) -> None:
        assert _humanize_anomaly_type("anomaly") == "Anomaly"

    def test_multiple_underscores(self) -> None:
        result = _humanize_anomaly_type("very_long_anomaly_type")
        assert result == "Very Long Anomaly Type"


# ===================================================================
# NotificationService — send_ha_notification
# ===================================================================


class TestSendHaNotification:
    """Tests for the primary HA notification method."""

    async def test_calls_ha_service(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        await service.send_ha_notification("Test Title", "Test message")

        ha_client.call_service.assert_awaited_once_with(
            "notify",
            "notify",
            data={"title": "Test Title", "message": "Test message"},
        )

    async def test_records_success_in_history(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        await service.send_ha_notification("Title", "Body")

        history = service.history
        assert len(history) == 1
        assert history[0].title == "Title"
        assert history[0].message == "Body"
        assert history[0].success is True
        assert history[0].error is None
        assert history[0].channel == "home_assistant"
        assert history[0].target == "default"

    async def test_with_custom_target(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        await service.send_ha_notification("Alert", "msg", target="mobile_app_phone")

        ha_client.call_service.assert_awaited_once_with(
            "notify",
            "mobile_app_phone",
            data={"title": "Alert", "message": "msg"},
        )
        assert service.history[0].target == "mobile_app_phone"

    async def test_failure_records_error_and_reraises(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        ha_client.call_service.side_effect = HAClientError("HA unreachable")

        with pytest.raises(HAClientError, match="HA unreachable"):
            await service.send_ha_notification("Fail", "msg")

        history = service.history
        assert len(history) == 1
        assert history[0].success is False
        assert history[0].error == "HA unreachable"


# ===================================================================
# NotificationService — send (convenience wrapper)
# ===================================================================


class TestSend:
    """Tests for the send() convenience method."""

    async def test_delegates_to_send_ha_notification(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        await service.send("Quick Title", "Quick message")

        ha_client.call_service.assert_awaited_once_with(
            "notify",
            "notify",
            data={"title": "Quick Title", "message": "Quick message"},
        )


# ===================================================================
# NotificationService — notify_anomaly
# ===================================================================


class TestNotifyAnomaly:
    """Tests for the anomaly notification helper."""

    async def test_builds_correct_title_and_message(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        await service.notify_anomaly("Kitchen", "temperature_spike", "32°C detected")

        ha_client.call_service.assert_awaited_once()
        call_args = ha_client.call_service.call_args
        data = call_args.kwargs["data"]
        assert data["title"] == "Anomaly Detected: Kitchen"
        assert "Temperature Spike" in data["message"]
        assert "Kitchen" in data["message"]
        assert "32°C detected" in data["message"]

    async def test_swallows_ha_errors(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        ha_client.call_service.side_effect = HAClientError("HA down")

        # Should NOT raise
        await service.notify_anomaly("Bedroom", "sensor_offline", "No data for 10m")

        # But should still record the failure in history
        assert len(service.history) == 1
        assert service.history[0].success is False


# ===================================================================
# NotificationService — notify_comfort_issue
# ===================================================================


class TestNotifyComfortIssue:
    """Tests for the comfort deviation notification helper."""

    async def test_above_target(self, service: NotificationService, ha_client: AsyncMock) -> None:
        await service.notify_comfort_issue("Living Room", 25.5, 22.0)

        call_args = ha_client.call_service.call_args
        data = call_args.kwargs["data"]
        assert data["title"] == "Comfort Alert: Living Room"
        assert "above" in data["message"]
        assert "3.5" in data["message"]
        assert "25.5" in data["message"]
        assert "22.0" in data["message"]

    async def test_below_target(self, service: NotificationService, ha_client: AsyncMock) -> None:
        await service.notify_comfort_issue("Office", 18.0, 21.0)

        call_args = ha_client.call_service.call_args
        data = call_args.kwargs["data"]
        assert "below" in data["message"]
        assert "3.0" in data["message"]

    async def test_swallows_ha_errors(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        ha_client.call_service.side_effect = HAClientError("HA down")

        # Should NOT raise
        await service.notify_comfort_issue("Garage", 30.0, 22.0)


# ===================================================================
# NotificationService — history management
# ===================================================================


class TestHistory:
    """Tests for history tracking and limits."""

    async def test_history_returns_copy(
        self, service: NotificationService, ha_client: AsyncMock
    ) -> None:
        await service.send_ha_notification("T", "M")
        h1 = service.history
        h2 = service.history
        assert h1 is not h2
        assert h1 == h2

    async def test_clear_history(self, service: NotificationService, ha_client: AsyncMock) -> None:
        await service.send_ha_notification("T", "M")
        assert len(service.history) == 1

        service.clear_history()
        assert len(service.history) == 0

    async def test_history_limit_enforced(
        self,
        small_history_service: NotificationService,
        ha_client: AsyncMock,
    ) -> None:
        svc = small_history_service
        for i in range(5):
            await svc.send_ha_notification(f"Title {i}", f"Message {i}")

        # Limit is 3, so only the last 3 should remain
        assert len(svc.history) == 3
        assert svc.history[0].title == "Title 2"
        assert svc.history[1].title == "Title 3"
        assert svc.history[2].title == "Title 4"
