"""Notification service for ClimateIQ.

Sends notifications through Home Assistant, external webhooks, and
provides convenience methods for common alert scenarios (anomalies,
comfort issues).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.integrations.ha_client import HAClient, HAClientError

logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT = 10.0  # seconds


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NotificationTarget:
    """Describes a notification destination."""

    channel: str  # e.g. "ha", "webhook", "mobile_app"
    address: str  # e.g. entity_id, URL, device name


@dataclass(slots=True)
class NotificationRecord:
    """Immutable record of a sent notification (for auditing / dedup)."""

    title: str
    message: str
    target: str
    channel: str
    sent_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    success: bool = True
    error: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class NotificationService:
    """Send notifications through Home Assistant and external webhooks.

    Usage::

        service = NotificationService(ha_client)
        await service.send_ha_notification("Alert", "Temperature spike in Kitchen")
        await service.notify_anomaly("Kitchen", "temperature_spike", "32°C detected")
        await service.send_webhook("https://hooks.example.com/abc", {"event": "alert"})
    """

    def __init__(
        self,
        ha_client: HAClient,
        *,
        history_limit: int = 100,
    ) -> None:
        self._ha = ha_client
        self._history: list[NotificationRecord] = []
        self._history_limit = history_limit

    # ------------------------------------------------------------------
    # Public API — Home Assistant notifications
    # ------------------------------------------------------------------

    async def send_ha_notification(
        self,
        title: str,
        message: str,
        target: str | None = None,
    ) -> None:
        """Send a notification through Home Assistant's notify service.

        Args:
            title: Notification title.
            message: Notification body text.
            target: Optional HA notify target.  If ``None``, uses the
                    default ``notify.notify`` service.  If a string like
                    ``"mobile_app_phone"``, calls
                    ``notify.mobile_app_phone``.
        """
        service_name = target if target else "notify"
        data: dict[str, Any] = {
            "title": title,
            "message": message,
        }

        record = NotificationRecord(
            title=title,
            message=message,
            target=target or "default",
            channel="home_assistant",
        )

        try:
            await self._ha.call_service("notify", service_name, data=data)
            logger.info(
                "HA notification sent: [%s] %s → %s",
                title,
                message[:80],
                service_name,
            )
            record.success = True
        except HAClientError as exc:
            logger.error("Failed to send HA notification: %s", exc)
            record.success = False
            record.error = str(exc)
            raise
        finally:
            self._record(record)

    async def send(self, title: str, message: str) -> None:
        """Convenience method — send via the default HA notify service.

        Maintains backward compatibility with the original stub interface.
        """
        await self.send_ha_notification(title, message)

    # ------------------------------------------------------------------
    # Public API — Webhook notifications
    # ------------------------------------------------------------------

    async def send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        """POST a JSON payload to an external webhook URL.

        Args:
            url: The webhook endpoint URL.
            payload: Arbitrary JSON-serializable dict to send.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
            httpx.ConnectError: On network failures.
        """
        record = NotificationRecord(
            title="webhook",
            message=str(payload)[:200],
            target=url,
            channel="webhook",
        )

        try:
            async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()

            logger.info("Webhook delivered to %s (status %d)", url, response.status_code)
            record.success = True
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Webhook to %s failed with status %d: %s",
                url,
                exc.response.status_code,
                exc.response.text[:200],
            )
            record.success = False
            record.error = f"HTTP {exc.response.status_code}"
            raise
        except httpx.ConnectError as exc:
            logger.error("Webhook connection to %s failed: %s", url, exc)
            record.success = False
            record.error = str(exc)
            raise
        except Exception as exc:
            logger.exception("Unexpected error sending webhook to %s", url)
            record.success = False
            record.error = str(exc)
            raise
        finally:
            self._record(record)

    # ------------------------------------------------------------------
    # Public API — Domain-specific notifications
    # ------------------------------------------------------------------

    async def notify_anomaly(
        self,
        zone_name: str,
        anomaly_type: str,
        details: str,
    ) -> None:
        """Send a notification about a detected anomaly.

        Args:
            zone_name: Human-readable zone name (e.g. "Kitchen").
            anomaly_type: Type of anomaly (e.g. "temperature_spike",
                          "humidity_drop", "sensor_offline").
            details: Free-text description of the anomaly.
        """
        title = f"Anomaly Detected: {zone_name}"
        message = (
            f"A {_humanize_anomaly_type(anomaly_type)} anomaly was detected "
            f"in {zone_name}.\n\n"
            f"Details: {details}"
        )

        try:
            await self.send_ha_notification(title, message)
        except HAClientError:
            # Log but don't propagate — anomaly notifications are best-effort
            logger.warning(
                "Could not deliver anomaly notification for %s via HA",
                zone_name,
            )

    async def notify_comfort_issue(
        self,
        zone_name: str,
        current_temp: float,
        target_temp: float,
    ) -> None:
        """Send a notification about a comfort deviation.

        Args:
            zone_name: Human-readable zone name.
            current_temp: Current measured temperature.
            target_temp: Desired target temperature.
        """
        delta = current_temp - target_temp
        direction = "above" if delta > 0 else "below"
        title = f"Comfort Alert: {zone_name}"
        message = (
            f"{zone_name} is {abs(delta):.1f}° {direction} the target temperature.\n"
            f"Current: {current_temp:.1f}°  |  Target: {target_temp:.1f}°"
        )

        try:
            await self.send_ha_notification(title, message)
        except HAClientError:
            logger.warning(
                "Could not deliver comfort notification for %s via HA",
                zone_name,
            )

    # ------------------------------------------------------------------
    # History / introspection
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[NotificationRecord]:
        """Return a copy of the recent notification history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear the notification history."""
        self._history.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record(self, record: NotificationRecord) -> None:
        """Append a record to the history ring buffer."""
        self._history.append(record)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _humanize_anomaly_type(anomaly_type: str) -> str:
    """Convert snake_case anomaly types to human-readable labels."""
    return anomaly_type.replace("_", " ").title()


__all__ = [
    "NotificationRecord",
    "NotificationService",
    "NotificationTarget",
]
