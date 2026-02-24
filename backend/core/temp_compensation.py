"""Temperature offset compensation for single-thermostat systems.

ClimateIQ controls a single thermostat (e.g. Ecobee) that measures
temperature at its own location (typically a hallway).  When the
priority zone is in a different room, the thermostat's reading may
differ significantly from the actual room temperature.

This module computes an adjusted setpoint so the thermostat runs
long enough to bring the priority zone to the desired temperature.
"""

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers for reading live sensor temperatures from Home Assistant
# ---------------------------------------------------------------------------

async def _get_ha_temp_unit(ha_client: Any) -> str:
    """Detect HA's configured temperature unit ('°F' or '°C')."""
    try:
        config = await ha_client.get_config()
        return config.get("unit_system", {}).get("temperature", "°C")
    except Exception:
        return "°C"


async def _get_live_zone_temp_c(
    ha_client: Any,
    zone: Any,
    ha_temp_unit: str,
) -> float | None:
    """Read the live temperature for a zone from HA sensors.

    Iterates through the zone's sensors, queries HA for each one,
    and returns the first valid temperature reading in Celsius.
    Sensors that are unavailable/unknown in HA are skipped.
    """
    if not zone.sensors:
        return None

    for sensor in zone.sensors:
        if not sensor.ha_entity_id:
            continue
        try:
            state = await ha_client.get_state(sensor.ha_entity_id)
            if not state or state.state in ("unavailable", "unknown", None):
                continue
            attrs = state.attributes or {}
            device_class = attrs.get("device_class", "")
            if device_class != "temperature":
                continue
            raw = float(state.state)
            uom = str(attrs.get("unit_of_measurement", ha_temp_unit))
            if "F" in uom.upper():
                raw = (raw - 32) * 5 / 9
            # Sanity check: reject impossible Celsius values
            if -40 <= raw <= 60:
                return raw
        except (ValueError, TypeError):
            continue
        except Exception:  # noqa: S112
            continue
    return None


async def _fetch_zones(db: Any, zone_ids: list[str] | None = None) -> list[Any]:
    """Fetch active zones (with sensors eagerly loaded) from the DB."""
    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import selectinload

    from backend.models.database import Zone

    stmt = sa_select(Zone).options(selectinload(Zone.sensors)).where(Zone.is_active.is_(True))
    if zone_ids:
        try:
            uuids = [uuid.UUID(str(zid)) for zid in zone_ids]
            stmt = stmt.where(Zone.id.in_(uuids))
        except (ValueError, AttributeError):
            pass

    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def get_priority_zone_temp_c(
    db: Any,
    zone_ids: list[str] | None = None,
    ha_client: Any | None = None,
) -> tuple[float | None, str | None, int]:
    """Get the current temperature for offset compensation.

    When multiple zones share the highest priority and have
    readings, their temperatures are **averaged** so the system
    balances between them rather than picking an arbitrary winner.

    Reads live sensor data from Home Assistant.  Sensors that are
    unavailable or offline in HA are skipped.

    Args:
        db: AsyncSession
        zone_ids: Optional list of zone UUID strings to consider.
                  If empty/None, considers ALL active zones.
        ha_client: HAClient instance for live sensor reads.

    Returns:
        (temperature_c, zone_name, zone_priority) or (None, None, 0)
        if no zone has a reading.  ``zone_name`` is a
        comma-separated string when multiple zones are averaged.
    """
    zones = await _fetch_zones(db, zone_ids)
    if not zones:
        return None, None, 0

    ha_temp_unit = await _get_ha_temp_unit(ha_client) if ha_client else "°C"

    # Sort by priority descending (highest priority first)
    zones.sort(key=lambda z: getattr(z, "priority", 5), reverse=True)

    # First pass: find the highest priority level that has at least one reading
    top_priority: int | None = None
    zone_readings: list[tuple[str, float]] = []  # (zone_name, temp_c)

    for zone in zones:
        zone_priority = getattr(zone, "priority", 5)

        # If we already found readings at a higher priority, skip lower ones
        if top_priority is not None and zone_priority < top_priority:
            break

        temp_c: float | None = None
        if ha_client:
            temp_c = await _get_live_zone_temp_c(ha_client, zone, ha_temp_unit)

        if temp_c is not None:
            if top_priority is None:
                top_priority = zone_priority
            zone_readings.append((zone.name, temp_c))

    if not zone_readings:
        return None, None, 0

    # Average the temperatures across all zones at the top priority
    avg_temp = sum(t for _, t in zone_readings) / len(zone_readings)
    zone_names = ", ".join(name for name, _ in zone_readings)

    if len(zone_readings) > 1:
        temps_str = ", ".join(f"{name}={t:.1f}C" for name, t in zone_readings)
        logger.debug(
            "Averaging %d zones at priority %d: %s -> %.1f C",
            len(zone_readings), top_priority, temps_str, avg_temp,
        )

    return avg_temp, zone_names, top_priority or 0


async def get_avg_zone_temp_c(
    db: Any,
    zone_ids: list[str] | None = None,
    ha_client: Any | None = None,
) -> tuple[float | None, str | None]:
    """Get the average temperature across zones (ignoring priority).

    Unlike ``get_priority_zone_temp_c`` which only averages zones at
    the highest priority tier, this function averages ALL zones that
    have a live reading from Home Assistant.

    Sensors that are unavailable or offline in HA are skipped.

    Args:
        db: AsyncSession
        zone_ids: Optional list of zone UUID strings to consider.
                  If empty/None, considers ALL active zones.
        ha_client: HAClient instance for live sensor reads.

    Returns:
        (avg_temp_c, zone_names) or (None, None) if no readings.
        ``zone_names`` is a comma-separated string of contributing zones.
    """
    zones = await _fetch_zones(db, zone_ids)
    if not zones:
        return None, None

    ha_temp_unit = await _get_ha_temp_unit(ha_client) if ha_client else "°C"
    readings: list[tuple[str, float]] = []  # (zone_name, temp_c)

    for zone in zones:
        temp_c: float | None = None
        if ha_client:
            temp_c = await _get_live_zone_temp_c(ha_client, zone, ha_temp_unit)

        if temp_c is not None:
            readings.append((zone.name, temp_c))

    if not readings:
        return None, None

    avg_temp = sum(t for _, t in readings) / len(readings)
    zone_names = ", ".join(name for name, _ in readings)
    return avg_temp, zone_names


async def compute_adjusted_setpoint(
    desired_temp_c: float,
    thermostat_reading_c: float,
    priority_zone_temp_c: float,
    max_offset_f: float = 8.0,
) -> tuple[float, float]:
    """Compute the adjusted setpoint to compensate for sensor location.

    Args:
        desired_temp_c: The temperature we want in the priority zone (Celsius).
        thermostat_reading_c: What the thermostat currently reads (Celsius).
        priority_zone_temp_c: What the priority zone's sensor reads (Celsius).
        max_offset_f: Maximum allowed offset in Fahrenheit.

    Returns:
        (adjusted_temp_c, offset_c) -- the adjusted setpoint and the
        offset that was applied (both in Celsius).
    """
    # Offset = thermostat reading - priority zone reading
    # If thermostat reads higher than the zone, offset is positive
    # and we need to increase the setpoint sent to the thermostat.
    offset_c = thermostat_reading_c - priority_zone_temp_c

    # Convert max offset from F to C for clamping
    max_offset_c = max_offset_f * 5.0 / 9.0

    # Clamp the offset
    clamped_offset_c = max(min(offset_c, max_offset_c), -max_offset_c)

    adjusted_temp_c = desired_temp_c + clamped_offset_c

    if abs(clamped_offset_c) > 0.1:
        logger.info(
            "Offset compensation: desired=%.1f C, thermostat=%.1f C, "
            "zone=%.1f C, raw_offset=%.1f C, clamped=%.1f C, adjusted=%.1f C",
            desired_temp_c,
            thermostat_reading_c,
            priority_zone_temp_c,
            offset_c,
            clamped_offset_c,
            adjusted_temp_c,
        )

    return adjusted_temp_c, clamped_offset_c


async def get_thermostat_reading_c(ha_client: Any, climate_entity: str) -> float | None:
    """Get the thermostat's current temperature reading in Celsius.

    Args:
        ha_client: HAClient instance.
        climate_entity: Entity ID (e.g. "climate.thermostat").

    Returns:
        Current temperature in Celsius, or None if unavailable.
    """
    try:
        state = await ha_client.get_state(climate_entity)
        if not state:
            return None
        current_temp = state.attributes.get("current_temperature")
        if current_temp is None:
            return None
        current_temp = float(current_temp)

        # Check if HA is in Fahrenheit
        ha_config = await ha_client.get_config()
        ha_temp_unit = ha_config.get("unit_system", {}).get("temperature", "\u00b0C")
        if ha_temp_unit == "\u00b0F":
            current_temp = (current_temp - 32) * 5 / 9

        return current_temp
    except Exception as exc:
        logger.debug("Could not read thermostat temperature: %s", exc)
        return None


async def get_max_offset_setting(db: Any) -> float:
    """Read the max_temp_offset_f setting from the database.

    Returns the value in Fahrenheit (default 8.0).
    """
    from sqlalchemy import select as sa_select

    from backend.models.database import SystemSetting

    try:
        result = await db.execute(
            sa_select(SystemSetting).where(SystemSetting.key == "max_temp_offset_f")
        )
        row = result.scalar_one_or_none()
        if row and row.value:
            val = row.value.get("value", 8.0)
            return float(val)
    except Exception as exc:
        logger.debug("Could not read max_temp_offset_f setting: %s", exc)
    return 8.0


async def apply_offset_compensation(
    db: Any,
    ha_client: Any,
    climate_entity: str,
    desired_temp_c: float,
    zone_ids: list[str] | None = None,
) -> tuple[float, float, str | None]:
    """High-level function: compute and return the adjusted setpoint.

    This is the main entry point for offset compensation. Call this
    before converting to HA units and sending to set_temperature.

    Args:
        db: AsyncSession
        ha_client: HAClient instance
        climate_entity: Entity ID
        desired_temp_c: What we want the priority zone to be (Celsius)
        zone_ids: Optional zone IDs to consider (from schedule).
                  None = all active zones (for Follow-Me / Active mode).

    Returns:
        (adjusted_temp_c, offset_c, zone_names)
        If compensation cannot be computed (missing data), returns
        (desired_temp_c, 0.0, None) -- i.e. no adjustment.
    """
    # 1. Get the average temperature across ALL schedule zones (live from HA)
    avg_temp_c, zone_names = await get_avg_zone_temp_c(db, zone_ids, ha_client=ha_client)
    if avg_temp_c is None:
        logger.debug("No zone temperature available, skipping offset compensation")
        return desired_temp_c, 0.0, None

    # 2. Get the thermostat's current reading
    thermostat_c = await get_thermostat_reading_c(ha_client, climate_entity)
    if thermostat_c is None:
        logger.debug("No thermostat reading available, skipping offset compensation")
        return desired_temp_c, 0.0, zone_names

    # 3. Get the max offset setting
    max_offset_f = await get_max_offset_setting(db)

    # 4. Compute the adjusted setpoint using the zone average
    adjusted_c, offset_c = await compute_adjusted_setpoint(
        desired_temp_c, thermostat_c, avg_temp_c, max_offset_f
    )

    return adjusted_c, offset_c, zone_names
