"""Chat API routes for ClimateIQ AI assistant."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db
from backend.config import get_settings
from backend.integrations.llm.provider import LLMProvider
from backend.integrations.llm.tools import get_climate_tools
from backend.models.database import Conversation, Zone

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Pydantic Models
# ============================================================================


class ChatMessage(BaseModel):
    """Chat message from user."""

    message: str = Field(..., min_length=1, max_length=10000)
    session_id: str | None = None
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """Response from AI assistant."""

    message: str
    session_id: str
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class ConversationHistoryItem(BaseModel):
    """A conversation history item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: str
    user_message: str
    assistant_response: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> ConversationHistoryItem:
        """Handle SQLAlchemy Conversation model where the column is ``metadata_``.

        The ORM ``Conversation`` stores the JSONB column as ``metadata_``
        (Python attr) mapped to ``"metadata"`` (DB column) and exposes a
        ``meta`` property.  ``DeclarativeBase`` already occupies the plain
        ``metadata`` attribute with the SQLAlchemy ``MetaData`` registry, so
        we must read the value through ``metadata_`` or ``meta``.
        """
        if hasattr(obj, "metadata_"):
            # Build a dict so Pydantic doesn't touch the SA MetaData descriptor.
            data = {
                "id": obj.id,
                "session_id": obj.session_id,
                "user_message": obj.user_message,
                "assistant_response": obj.assistant_response,
                "created_at": obj.created_at,
                "metadata": obj.metadata_ if obj.metadata_ is not None else {},
            }
            return super().model_validate(data, **kwargs)
        return super().model_validate(obj, **kwargs)


class CommandRequest(BaseModel):
    """Voice/text command request."""

    command: str = Field(..., min_length=1, max_length=1000)
    zone_id: uuid.UUID | None = None


class CommandResponse(BaseModel):
    """Command execution response."""

    success: bool
    message: str
    action: str | None = None
    zone_affected: str | None = None
    new_value: Any | None = None


# ============================================================================
# Directive / Memory Helpers
# ============================================================================

DIRECTIVE_EXTRACTION_PROMPT = """Analyze the following conversation exchange and extract any user preferences, constraints, or directives about their HVAC/climate system. Only extract actionable, long-term preferences — NOT one-time requests.

Examples of directives to extract:
- "Never heat the basement above 65F"
- "I prefer it cooler at night"
- "Always keep the nursery at 72F"
- "Don't run the AC when outdoor temp is below 60F"
- "I work from home on Mondays and Fridays"

For each directive found, output a JSON array of objects with:
- "directive": the preference in clear, concise language
- "category": one of "preference", "constraint", "schedule_hint", "comfort", "energy"
- "zone_name": the zone name if zone-specific, or null

If no directives are found, return an empty array: []

IMPORTANT: Return ONLY the JSON array, no other text.

User message: {user_message}
Assistant response: {assistant_response}"""


async def _extract_directives(
    user_message: str,
    assistant_response: str,
    conversation_id: uuid.UUID,
    db: AsyncSession,
    zones: list[Zone],
) -> None:
    """Extract user directives from a conversation and persist them.

    Runs as a fire-and-forget background task after each chat message.
    Uses a lightweight LLM call to identify actionable preferences.
    """
    import json as _json

    from backend.models.database import UserDirective

    try:
        llm = await get_llm_provider()
    except Exception:
        return

    try:
        response = await llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": DIRECTIVE_EXTRACTION_PROMPT.format(
                        user_message=user_message,
                        assistant_response=assistant_response,
                    ),
                }
            ],
            system="You extract user preferences from HVAC conversations. Return only valid JSON.",
        )

        content = response.get("content", "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        directives = _json.loads(content)
        if not isinstance(directives, list):
            return

        # Build zone name -> id map for matching
        zone_map = {z.name.lower(): z.id for z in zones}

        for d in directives:
            if not isinstance(d, dict) or not d.get("directive"):
                continue

            directive_text = str(d["directive"]).strip()
            if not directive_text:
                continue

            category = d.get("category", "preference")
            if category not in ("preference", "constraint", "schedule_hint", "comfort", "energy"):
                category = "preference"

            # Try to match zone
            zone_id: uuid.UUID | None = None
            zone_name = d.get("zone_name")
            if zone_name and isinstance(zone_name, str):
                zone_id = zone_map.get(zone_name.lower())

            # Check for duplicates (same directive text)
            existing = await db.execute(
                select(UserDirective).where(
                    UserDirective.directive == directive_text,
                    UserDirective.is_active.is_(True),
                )
            )
            if existing.scalar_one_or_none():
                continue

            db.add(UserDirective(
                directive=directive_text,
                source_conversation_id=conversation_id,
                zone_id=zone_id,
                category=category,
            ))

        await db.commit()
        logger.info("Extracted %d directive(s) from conversation %s", len(directives), conversation_id)

    except Exception as e:
        logger.debug("Directive extraction failed (non-critical): %s", e)


async def _get_active_directives(db: AsyncSession) -> str:
    """Load all active user directives for injection into prompts."""
    from sqlalchemy.orm import selectinload

    from backend.models.database import UserDirective

    result = await db.execute(
        select(UserDirective)
        .where(UserDirective.is_active.is_(True))
        .options(selectinload(UserDirective.zone))
        .order_by(UserDirective.created_at.asc())
    )
    directives = result.scalars().all()

    if not directives:
        return ""

    lines = ["=== USER PREFERENCES & DIRECTIVES (from past conversations) ===\n"]
    lines.append("IMPORTANT: These are standing user preferences. Always respect them unless the user explicitly overrides one in the current conversation.\n")
    for d in directives:
        zone_note = ""
        if d.zone_id and d.zone:
            zone_note = f" [zone: {d.zone.name}]"
        lines.append(f"- [{d.category}]{zone_note} {d.directive}")

    return "\n".join(lines)


# ============================================================================
# System Prompt
# ============================================================================

SYSTEM_PROMPT = """You are ClimateIQ Advisor, an intelligent HVAC management assistant. You help users understand and control their home climate system through natural conversation.

You have FULL visibility into the current system state, including the operating mode, thermostat status, active schedules, and all zone conditions. Use this information to give accurate, specific answers.

You can:
- Report the current system mode, thermostat state, and all zone conditions
- Adjust temperatures in specific zones or the whole house
- Check current temperatures, humidity, and occupancy
- Explain active schedules and suggest new ones
- Provide energy-saving recommendations
- Explain how ClimateIQ works in detail

When users request changes, use the available tools to execute them. Always confirm what action you're taking.

{logic_reference}

{directives}

=== CURRENT SYSTEM STATE ===

{system_state}

=== ZONE DETAILS ===

{zones}

=== SENSOR CONDITIONS ===

{conditions}

When users ask about the system mode, thermostat state, schedules, or any system configuration, answer directly from the system state above. Be concise, helpful, and proactive about energy savings while maintaining comfort."""


def _get_logic_reference_text() -> str:
    """Return the logic reference as plain text for the LLM system prompt."""
    sections = [
        ("System Architecture", [
            "ClimateIQ is a Home Assistant add-on with React frontend, FastAPI backend, TimescaleDB, and Redis.",
            "All sensor data flows from HA via WebSocket. One global thermostat controls the whole house.",
            "Backend stores temps in Celsius. Frontend converts to user's preferred unit.",
        ]),
        ("Operating Modes", [
            "Learn: Passive observation, no HVAC changes.",
            "Scheduled: Follows user schedules. Executor checks every 60s, fires within 2-min window.",
            "Follow-Me: Tracks occupancy every 90s. Sets thermostat to occupied zone's comfort temp. Averages if multiple zones occupied. Eco temp (18°C) if none occupied. Dead-band of 0.5°C.",
            "Active/AI: Full LLM control every 5min. Gathers all data, asks LLM for optimal temp with reasoning. Safety clamped.",
        ]),
        ("Schedules", [
            "Each schedule: name, zone, days, start/end time, target temp (Celsius), HVAC mode, priority 1-10.",
            "Higher priority wins conflicts. Dedup prevents double-firing.",
        ]),
        ("Zones & Sensors", [
            "Zones = rooms. Current temp/humidity from per-zone Zigbee sensors ONLY, not the thermostat.",
            "Target setpoint is shared from the global thermostat.",
            "Comfort preferences per zone used by Follow-Me mode.",
        ]),
        ("Thermostat", [
            "Global climate entity (e.g., climate.ecobee). Ecobee uses target_temp_low (heat), target_temp_high (cool).",
            "Quick actions: Eco, Away, Boost Heat (+2°), Boost Cool (-2°).",
        ]),
        ("Notifications", [
            "Push via HA mobile app (notify.mobile_app_*). Alerts for: schedules, sensor offline, mode changes.",
        ]),
        ("Energy", [
            "Only shown when a real HA energy entity is configured. No heuristic estimates.",
        ]),
        ("Weather", [
            "Polled every 15min from HA weather entity, cached in Redis. Used by AI mode and chat context.",
        ]),
    ]

    lines = ["=== ClimateIQ System Logic Reference ===\n"]
    for title, details in sections:
        lines.append(f"\n## {title}")
        for d in details:
            lines.append(f"- {d}")
    return "\n".join(lines)


async def _get_live_system_context(db: AsyncSession, temperature_unit: str) -> str:
    """Gather live system state for the LLM context."""
    from sqlalchemy import select as _sel

    from backend.models.database import Schedule, SystemConfig, SystemSetting, Zone

    sections: list[str] = []
    kv: dict[str, Any] = {}

    # 1. Current system mode
    try:
        result = await db.execute(_sel(SystemConfig).limit(1))
        config = result.scalar_one_or_none()
        if config:
            sections.append(f"Current system mode: {config.current_mode.value}")
        else:
            sections.append("Current system mode: learn (default)")
    except Exception:
        sections.append("Current system mode: unknown")

    # 2. Key settings from system_settings KV table
    try:
        settings_result = await db.execute(_sel(SystemSetting))
        settings_rows = settings_result.scalars().all()
        for row in settings_rows:
            kv[row.key] = row.value.get("value") if isinstance(row.value, dict) else row.value

        settings_parts: list[str] = []
        if kv.get("temperature_unit"):
            settings_parts.append(f"Temperature unit: {kv['temperature_unit']}")
        if kv.get("climate_entities"):
            settings_parts.append(f"Climate entities: {kv['climate_entities']}")
        if kv.get("weather_entity"):
            settings_parts.append(f"Weather entity: {kv['weather_entity']}")
        if kv.get("energy_entity"):
            settings_parts.append(f"Energy entity: {kv['energy_entity']}")
        if kv.get("default_comfort_temp_min") and kv.get("default_comfort_temp_max"):
            min_c = float(kv["default_comfort_temp_min"])
            max_c = float(kv["default_comfort_temp_max"])
            min_d, unit = _format_temp_for_display(min_c, temperature_unit)
            max_d, _ = _format_temp_for_display(max_c, temperature_unit)
            settings_parts.append(f"Default comfort range: {min_d:.1f}-{max_d:.1f}\u00b0{unit}")
        if kv.get("notification_target"):
            settings_parts.append(f"Notification target: {kv['notification_target']}")

        if settings_parts:
            sections.append("System settings:\n" + "\n".join(f"  - {p}" for p in settings_parts))
    except Exception as e:
        sections.append(f"System settings: unavailable ({e})")

    # 3. Global thermostat state
    try:
        import backend.api.dependencies as _deps

        ha_client = _deps._ha_client
        if ha_client:
            # Get climate entity from settings or config
            climate_entity = kv.get("climate_entities", "")
            if isinstance(climate_entity, str) and climate_entity.strip():
                entity_id = climate_entity.strip().split(",")[0].strip()
            else:
                from backend.config import get_settings as _gs

                _s = _gs()
                entity_id = (
                    _s.climate_entities.strip().split(",")[0].strip()
                    if _s.climate_entities.strip()
                    else ""
                )

            if entity_id:
                state = await ha_client.get_state(entity_id)
                if state:
                    attrs = state.attributes or {}
                    hvac_mode = state.state  # "heat", "cool", "auto", "off"
                    current = attrs.get("current_temperature")
                    target = (
                        attrs.get("temperature")
                        or attrs.get("target_temp_low")
                        or attrs.get("target_temp_high")
                    )
                    hvac_action = attrs.get("hvac_action", "unknown")

                    unit_label = (
                        "F" if temperature_unit.upper() in ("F", "FAHRENHEIT") else "C"
                    )
                    thermo_parts = [f"Entity: {entity_id}"]
                    thermo_parts.append(f"HVAC mode: {hvac_mode}")
                    thermo_parts.append(f"HVAC action: {hvac_action}")
                    if current is not None:
                        thermo_parts.append(
                            f"Current temperature (from thermostat): {current}\u00b0{unit_label}"
                        )
                    if target is not None:
                        thermo_parts.append(
                            f"Target temperature: {target}\u00b0{unit_label}"
                        )
                    if attrs.get("target_temp_low") is not None:
                        thermo_parts.append(
                            f"Target temp low: {attrs['target_temp_low']}"
                        )
                    if attrs.get("target_temp_high") is not None:
                        thermo_parts.append(
                            f"Target temp high: {attrs['target_temp_high']}"
                        )
                    if attrs.get("preset_mode"):
                        thermo_parts.append(f"Preset mode: {attrs['preset_mode']}")
                    if attrs.get("fan_mode"):
                        thermo_parts.append(f"Fan mode: {attrs['fan_mode']}")

                    sections.append(
                        "Thermostat state:\n"
                        + "\n".join(f"  - {p}" for p in thermo_parts)
                    )
    except Exception as e:
        sections.append(f"Thermostat state: unavailable ({e})")

    # 4. Active schedules
    try:
        sched_result = await db.execute(
            _sel(Schedule)
            .where(Schedule.is_enabled.is_(True))
            .order_by(Schedule.priority.desc())
        )
        schedules = sched_result.scalars().all()
        if schedules:
            # Collect zone IDs across all schedules
            all_zone_ids: set[str] = set()
            for s in schedules:
                if s.zone_ids and isinstance(s.zone_ids, list):
                    all_zone_ids.update(str(zid) for zid in s.zone_ids)

            zone_name_map: dict[str, str] = {}
            if all_zone_ids:
                import uuid as _uuid

                zone_uuids = []
                for zid in all_zone_ids:
                    try:
                        zone_uuids.append(_uuid.UUID(zid))
                    except ValueError:
                        pass
                if zone_uuids:
                    zr = await db.execute(
                        _sel(Zone).where(Zone.id.in_(zone_uuids))
                    )
                    zone_name_map = {
                        str(z.id): z.name for z in zr.scalars().all()
                    }

            sched_lines: list[str] = []
            for s in schedules:
                zone_names_list: list[str] = []
                if s.zone_ids and isinstance(s.zone_ids, list):
                    for zid in s.zone_ids:
                        zname = zone_name_map.get(str(zid))
                        if zname:
                            zone_names_list.append(zname)
                zone_display = (
                    ", ".join(zone_names_list) if zone_names_list else "All zones"
                )

                target_display_val, target_unit = _format_temp_for_display(
                    s.target_temp_c, temperature_unit
                )

                days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                day_str = ",".join(
                    days[d] for d in (s.days_of_week or []) if 0 <= d <= 6
                )

                sched_lines.append(
                    f'  - "{s.name}": {zone_display} | {day_str} | '
                    f"{s.start_time}{'-' + s.end_time if s.end_time else ''} | "
                    f"{target_display_val:.1f}\u00b0{target_unit} | "
                    f"{s.hvac_mode} | priority {s.priority}"
                )
            sections.append(
                f"Active schedules ({len(schedules)}):\n"
                + "\n".join(sched_lines)
            )
        else:
            sections.append("Active schedules: none")
    except Exception as e:
        sections.append(f"Active schedules: unavailable ({e})")

    # 5. Weather (from Redis cache)
    try:
        from backend.api.main import app_state

        redis = app_state.redis_client
        if redis:
            weather_json = await redis.get("weather:current")
            if weather_json:
                import json

                weather = json.loads(weather_json)
                w_parts: list[str] = []
                if weather.get("condition"):
                    w_parts.append(f"Condition: {weather['condition']}")
                if weather.get("temperature") is not None:
                    w_parts.append(f"Outdoor temp: {weather['temperature']}\u00b0")
                if weather.get("humidity") is not None:
                    w_parts.append(f"Outdoor humidity: {weather['humidity']}%")
                if weather.get("wind_speed") is not None:
                    w_parts.append(f"Wind: {weather['wind_speed']}")
                if w_parts:
                    sections.append(
                        "Weather:\n" + "\n".join(f"  - {p}" for p in w_parts)
                    )
    except Exception:  # noqa: S110
        pass  # Weather is optional, don't add noise

    return "\n\n".join(sections) if sections else "No system state available."


# ============================================================================
# Helper Functions
# ============================================================================


async def get_llm_provider() -> LLMProvider:
    """Get configured LLM provider."""
    settings = get_settings()

    # Determine which provider to use based on available keys
    if settings.anthropic_api_key:
        return LLMProvider(
            provider="anthropic",
            api_key=settings.anthropic_api_key,
        )
    elif settings.openai_api_key:
        return LLMProvider(
            provider="openai",
            api_key=settings.openai_api_key,
        )
    elif settings.gemini_api_key:
        return LLMProvider(
            provider="gemini",
            api_key=settings.gemini_api_key,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No LLM provider configured. Please add an API key in settings.",
        )


async def get_zone_context(db: AsyncSession, temperature_unit: str) -> str:
    """Get current zone information for context.

    Queries DB sensor readings first, then falls back to live HA sensor
    states so the LLM never sees a zone as "offline" when HA still has data.
    """
    from sqlalchemy.orm import selectinload

    from backend.models.database import SensorReading

    result = await db.execute(
        select(Zone)
        .where(Zone.is_active.is_(True))
        .options(selectinload(Zone.sensors), selectinload(Zone.devices))
    )
    zones = list(result.scalars().unique().all())

    if not zones:
        return "No zones configured."

    # Try to get HA client for live fallback
    ha_client = None
    try:
        import backend.api.dependencies as _deps
        ha_client = _deps._ha_client
    except Exception:  # noqa: S110
        pass

    zone_info = []
    for zone in zones:
        details = [f"- {zone.name} (ID: {zone.id}, status: ONLINE)"]
        sensor_count = len(zone.sensors) if zone.sensors else 0
        details.append(f"[{sensor_count} sensor(s)]")

        temp_c: float | None = None

        # 1) Try DB readings
        if zone.sensors:
            reading_result = await db.execute(
                select(SensorReading)
                .where(SensorReading.sensor_id.in_([s.id for s in zone.sensors]))
                .order_by(SensorReading.recorded_at.desc())
                .limit(10)
            )
            readings = reading_result.scalars().all()
            temp_c = next(
                (r.temperature_c for r in readings if r.temperature_c is not None),
                None,
            )

        # 2) Fallback: try live HA sensor entities
        if temp_c is None and ha_client and zone.sensors:
            for sensor in zone.sensors:
                if not sensor.ha_entity_id:
                    continue
                try:
                    state = await ha_client.get_state(sensor.ha_entity_id)
                    if state and state.state not in ("unavailable", "unknown", None):
                        try:
                            raw = float(state.state)
                            # HA may report in F — check unit_of_measurement
                            uom = (state.attributes or {}).get("unit_of_measurement", "")
                            if "F" in str(uom).upper():
                                raw = (raw - 32) * 5 / 9
                            temp_c = raw
                            break
                        except (ValueError, TypeError):
                            pass
                except Exception:  # noqa: S110
                    pass

        if temp_c is not None:
            display_temp, display_unit = _format_temp_for_display(temp_c, temperature_unit)
            details.append(f"temp {display_temp:.1f}\u00b0{display_unit}")
        elif zone.sensors:
            details.append("(awaiting sensor data)")
        else:
            details.append("(no sensors assigned)")

        zone_info.append(" ".join(details))

    return "\n".join(zone_info)


async def get_conditions_context(db: AsyncSession, temperature_unit: str) -> str:
    """Get current conditions from sensor data for LLM context.

    Queries DB sensor readings first, then falls back to live HA sensor
    states so the LLM never mistakes a zone as offline when HA has data.
    """
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from backend.models.database import SensorReading, Zone

        zones_result = await db.execute(
            select(Zone).where(Zone.is_active.is_(True)).options(selectinload(Zone.sensors))
        )
        zones = zones_result.scalars().unique().all()

        if not zones:
            return "No zones configured."

        # Try to get HA client for live fallback
        ha_client = None
        try:
            import backend.api.dependencies as _deps
            ha_client = _deps._ha_client
        except Exception:  # noqa: S110
            pass

        conditions = []
        for zone in zones:
            if not zone.sensors:
                conditions.append(f"- {zone.name}: no sensors assigned (zone is active)")
                continue

            # 1) Try DB readings
            reading_result = await db.execute(
                select(SensorReading)
                .where(SensorReading.sensor_id.in_([s.id for s in zone.sensors]))
                .order_by(SensorReading.recorded_at.desc())
                .limit(25)
            )
            readings = reading_result.scalars().all()
            current_temp: float | None = None
            current_humidity: float | None = None
            current_presence: bool | None = None
            for reading in readings:
                if current_temp is None and reading.temperature_c is not None:
                    current_temp = reading.temperature_c
                if current_humidity is None and reading.humidity is not None:
                    current_humidity = reading.humidity
                if current_presence is None and reading.presence is not None:
                    current_presence = reading.presence
                if (
                    current_temp is not None
                    and current_humidity is not None
                    and current_presence is not None
                ):
                    break

            # 2) Fallback: try live HA sensor entities for missing values
            if ha_client and (current_temp is None or current_humidity is None):
                for sensor in zone.sensors:
                    if not sensor.ha_entity_id:
                        continue
                    try:
                        state = await ha_client.get_state(sensor.ha_entity_id)
                        if state and state.state not in ("unavailable", "unknown", None):
                            attrs = state.attributes or {}
                            device_class = attrs.get("device_class", "")
                            uom = str(attrs.get("unit_of_measurement", ""))

                            if current_temp is None and device_class == "temperature":
                                try:
                                    raw = float(state.state)
                                    if "F" in uom.upper():
                                        raw = (raw - 32) * 5 / 9
                                    current_temp = raw
                                except (ValueError, TypeError):
                                    pass

                            if current_humidity is None and device_class == "humidity":
                                try:
                                    current_humidity = float(state.state)
                                except (ValueError, TypeError):
                                    pass
                    except Exception:  # noqa: S110
                        pass

            if (
                current_temp is not None
                or current_humidity is not None
                or current_presence is not None
            ):
                parts = [f"- {zone.name}:"]
                if current_temp is not None:
                    display_temp, display_unit = _format_temp_for_display(
                        current_temp,
                        temperature_unit,
                    )
                    parts.append(f"{display_temp:.1f}\u00b0{display_unit}")
                if current_humidity is not None:
                    parts.append(f"{current_humidity:.0f}% humidity")
                if current_presence is not None:
                    parts.append("occupied" if current_presence else "unoccupied")
                conditions.append(" ".join(parts))
            else:
                # Zone is active with sensors but no data from DB or HA
                conditions.append(
                    f"- {zone.name}: no data available yet "
                    f"(zone is active, {len(zone.sensors)} sensor(s) assigned)"
                )

        return "\n".join(conditions) if conditions else "No sensor data available."
    except Exception as e:
        return f"Sensor data unavailable: {e}"


def _generate_suggestions(zones_list: list[Any]) -> list[str]:
    """Generate contextual chat suggestions based on current state."""
    from datetime import UTC, datetime

    suggestions: list[str] = []
    now = datetime.now(UTC)
    hour = now.hour

    # Time-based suggestions
    if 6 <= hour < 9:
        suggestions.append("Set up my morning routine")
    elif 17 <= hour < 21:
        suggestions.append("Switch to evening comfort mode")
    elif 21 <= hour or hour < 6:
        suggestions.append("Set sleeping temperatures")

    # Zone-based suggestions
    if zones_list:
        zone_name = zones_list[0].name
        suggestions.append(f"What's the temperature in {zone_name}?")
        if len(zones_list) > 1:
            suggestions.append(f"Compare {zones_list[0].name} and {zones_list[1].name}")

    # Always-useful suggestions
    suggestions.append("Show energy usage summary")

    return suggestions[:3]  # Return at most 3


def _normalize_zone_name(name: str) -> str:
    return name.strip().lower()


def _match_zone(zone_name: str, zone: Zone) -> bool:
    normalized = _normalize_zone_name(zone_name)
    zone_label = _normalize_zone_name(zone.name)
    if not normalized or not zone_label:
        return False
    return normalized in zone_label or zone_label in normalized


def _parse_temp_unit(unit_raw: str | None, default_unit: str) -> str:
    if unit_raw:
        return unit_raw.strip().upper()
    return default_unit.strip().upper() if default_unit else "C"


def _convert_to_c(temp: float, unit: str) -> float:
    return (temp - 32) * 5 / 9 if unit.upper() == "F" else temp


def _convert_delta_to_c(delta: float, unit: str) -> float:
    return delta * 5 / 9 if unit.upper() == "F" else delta


def _format_temp_for_display(temp_c: float, unit: str) -> tuple[float, str]:
    target_unit = unit.upper()
    if target_unit == "F":
        return (temp_c * 9 / 5 + 32), "F"
    return temp_c, "C"


def _format_delta_for_display(delta_c: float, unit: str) -> tuple[float, str]:
    target_unit = unit.upper()
    if target_unit == "F":
        return (delta_c * 9 / 5), "F"
    return delta_c, "C"


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            import json as _json

            parsed = _json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _normalize_parsed_command(
    parsed: dict[str, Any] | None,
    zones: list[Zone],
    default_unit: str,
) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None

    action = parsed.get("action")
    if not isinstance(action, str):
        return None

    normalized: dict[str, Any] = dict(parsed)

    zone_id = parsed.get("zone_id")
    zone_name = parsed.get("zone_name") or parsed.get("zone")
    if zone_id and isinstance(zone_id, str):
        try:
            zone_id = uuid.UUID(zone_id)
        except ValueError:
            zone_id = None
    elif isinstance(zone_id, uuid.UUID):
        pass
    else:
        zone_id = None

    if zone_id is None and isinstance(zone_name, str):
        for zone in zones:
            if _match_zone(zone_name, zone):
                zone_id = zone.id
                normalized["zone_name"] = zone.name
                break

    if zone_id is not None:
        normalized["zone_id"] = zone_id

    unit = _parse_temp_unit(str(parsed.get("unit")) if parsed.get("unit") else None, default_unit)

    if action == "set_temperature":
        temp_val = parsed.get("temperature") or parsed.get("temperature_c") or parsed.get("temp")
        if temp_val is None:
            temp_float = None
        else:
            try:
                temp_float = float(temp_val)
            except (TypeError, ValueError):
                temp_float = None
        if temp_float is not None:
            normalized["temperature_c"] = _convert_to_c(temp_float, unit)
            normalized["input_unit"] = unit

    if action == "adjust_temperature":
        amount_val = parsed.get("amount") or parsed.get("amount_c")
        if amount_val is None:
            amount_float = None
        else:
            try:
                amount_float = float(amount_val)
            except (TypeError, ValueError):
                amount_float = None
        if amount_float is not None:
            normalized["amount_c"] = _convert_delta_to_c(amount_float, unit)
            normalized["input_unit"] = unit

    return normalized


def _get_temp_c(parsed: dict[str, Any], default_unit: str) -> float | None:
    if "temperature_c" in parsed and parsed["temperature_c"] is not None:
        try:
            return float(parsed["temperature_c"])
        except (TypeError, ValueError):
            return None
    temp_val = parsed.get("temperature")
    if temp_val is None:
        return None
    try:
        temp_float = float(temp_val)
    except (TypeError, ValueError):
        return None
    unit = _parse_temp_unit(parsed.get("input_unit"), default_unit)
    return _convert_to_c(temp_float, unit)


async def parse_command(
    command: str,
    zones: list[Zone],
    default_unit: str = "C",
) -> dict[str, Any] | None:
    """
    Parse a voice/text command into an action.

    Supports commands like:
    - "Set living room to 72 degrees"
    - "Turn off bedroom AC"
    - "Make it warmer in the kitchen"
    - "What's the temperature in the office?"
    """
    command_lower = command.lower()

    # Temperature adjustment patterns
    import re

    # Pattern: "set [zone] to [temp] degrees"
    temp_match = re.search(
        r"set\s+(.+?)\s+to\s+(\d+(?:\.\d+)?)\s*(?:degrees)?\s*°?\s*([fc])?",
        command_lower,
    )
    if temp_match:
        zone_name = temp_match.group(1)
        temp = float(temp_match.group(2))
        unit = _parse_temp_unit(temp_match.group(3), default_unit)
        temp_c = _convert_to_c(temp, unit)

        # Find matching zone
        for zone in zones:
            if _match_zone(zone_name, zone):
                return {
                    "action": "set_temperature",
                    "zone_id": zone.id,
                    "zone_name": zone.name,
                    "temperature_c": temp_c,
                    "input_unit": unit,
                }

    # Pattern: "make it warmer/cooler in [zone]" with optional amount
    adjust_match = re.search(r"make\s+it\s+(warmer|cooler)\s+in\s+(.+)", command_lower)
    if adjust_match:
        direction = adjust_match.group(1)
        zone_name = adjust_match.group(2)

        # Try to extract amount from the original command (e.g. "5 degrees warmer")
        amount_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:degrees?)?\s*°?\s*([fc])?",
            command_lower,
        )
        amount_raw = float(amount_match.group(1)) if amount_match else 2.0
        amount_unit = _parse_temp_unit(
            amount_match.group(2) if amount_match else None, default_unit
        )
        amount = _convert_delta_to_c(amount_raw, amount_unit)

        for zone in zones:
            if _match_zone(zone_name, zone):
                return {
                    "action": "adjust_temperature",
                    "zone_id": zone.id,
                    "zone_name": zone.name,
                    "direction": direction,
                    "amount_c": amount,
                    "input_unit": amount_unit,
                }

    # Pattern: "turn off/on [zone]"
    toggle_match = re.search(
        r"turn\s+(on|off)\s+(.+?)(?:\s+(?:hvac|ac|heat|heating|cooling))?$", command_lower
    )
    if toggle_match:
        state = toggle_match.group(1)
        zone_name = toggle_match.group(2)

        for zone in zones:
            if _match_zone(zone_name, zone):
                return {
                    "action": "toggle_zone",
                    "zone_id": zone.id,
                    "zone_name": zone.name,
                    "enabled": state == "on",
                }

    # Pattern: "what's the temperature in [zone]"
    query_match = re.search(r"what(?:'s|\s+is)\s+the\s+temp(?:erature)?\s+in\s+(.+)", command_lower)
    if query_match:
        zone_name = query_match.group(1)

        for zone in zones:
            if _match_zone(zone_name, zone):
                return {
                    "action": "query_temperature",
                    "zone_id": zone.id,
                    "zone_name": zone.name,
                }

    return None


# ============================================================================
# Tool Call Execution
# ============================================================================


async def _execute_tool_call(
    func_name: str,
    func_args: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    """Dispatch a tool call from the LLM and return the result."""
    from backend.config import get_settings as _get_settings
    from backend.integrations.ha_client import HAClient
    from backend.models.database import Device, Sensor, SensorReading

    settings = _get_settings()

    if func_name == "set_zone_temperature":
        zone_id = func_args.get("zone_id")
        target_c = func_args.get("target_c")
        if zone_id is None or target_c is None:
            return {"success": False, "error": "Missing zone_id or target_c"}

        # Safety clamp
        target_c = max(settings.safety_min_temp_c, min(settings.safety_max_temp_c, float(target_c)))

        if settings.home_assistant_token:
            device_result = await db.execute(
                select(Device).where(Device.zone_id == uuid.UUID(str(zone_id)))
            )
            devices = device_result.scalars().all()
            climate_device = next(
                (d for d in devices if d.ha_entity_id and d.type.value == "thermostat"),
                None,
            )
            if climate_device and climate_device.ha_entity_id:
                async with HAClient(
                    str(settings.home_assistant_url), settings.home_assistant_token
                ) as ha:
                    await ha.set_temperature(climate_device.ha_entity_id, target_c)
                return {"success": True, "temperature_set": target_c}
        return {"success": False, "error": "No thermostat device found or HA not configured"}

    elif func_name == "set_device_state":
        device_id = func_args.get("device_id")
        state = func_args.get("state", "")
        if not device_id:
            return {"success": False, "error": "Missing device_id"}

        if settings.home_assistant_token:
            device_result = await db.execute(
                select(Device).where(Device.id == uuid.UUID(str(device_id)))
            )
            device = device_result.scalar_one_or_none()
            if device and device.ha_entity_id:
                async with HAClient(
                    str(settings.home_assistant_url), settings.home_assistant_token
                ) as ha:
                    if state in ("on", "heat", "cool", "auto", "fan"):
                        await ha.turn_on(device.ha_entity_id)
                    elif state == "off":
                        await ha.turn_off(device.ha_entity_id)
                return {"success": True, "device_state": state}
        return {"success": False, "error": "Device not found or HA not configured"}

    elif func_name == "get_zone_status":
        zone_id = func_args.get("zone_id")
        if not zone_id:
            return {"success": False, "error": "Missing zone_id"}

        reading_stmt = (
            select(SensorReading)
            .join(Sensor, Sensor.id == SensorReading.sensor_id)
            .where(Sensor.zone_id == uuid.UUID(str(zone_id)))
            .order_by(SensorReading.recorded_at.desc())
            .limit(1)
        )
        reading_result = await db.execute(reading_stmt)
        reading = reading_result.scalar_one_or_none()
        if reading:
            return {
                "success": True,
                "temperature_c": reading.temperature_c,
                "humidity": reading.humidity,
                "presence": reading.presence,
            }
        return {"success": True, "temperature_c": None, "humidity": None, "presence": None}

    elif func_name == "get_weather":
        from dataclasses import asdict

        from backend.integrations.weather_service import WeatherService
        from backend.models.database import SystemSetting

        if not settings.home_assistant_token:
            return {"success": False, "error": "Home Assistant token not configured"}

        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "weather_entity")
        )
        weather_setting = result.scalar_one_or_none()
        weather_entity = (
            weather_setting.value.get("value", "")
            if weather_setting and weather_setting.value
            else ""
        )
        if not weather_entity:
            return {"success": False, "error": "No weather entity configured"}

        async with HAClient(str(settings.home_assistant_url), settings.home_assistant_token) as ha:
            service = WeatherService(ha, weather_entity=weather_entity)
            current = await service.get_current()
            forecast = await service.get_forecast(hours=12)

        current_dict = asdict(current)
        current_dict.pop("ozone", None)
        forecast_list = [asdict(entry) for entry in forecast]
        return {
            "success": True,
            "weather_entity": weather_entity,
            "current": current_dict,
            "forecast": forecast_list,
        }

    elif func_name == "create_schedule":
        from backend.models.database import Schedule, Zone

        zone_id = func_args.get("zone_id")
        entries = func_args.get("entries")
        timezone = func_args.get("timezone")
        overwrite = bool(func_args.get("overwrite", False))

        if not zone_id or not entries:
            return {"success": False, "error": "Missing zone_id or entries"}

        try:
            zone_uuid = uuid.UUID(str(zone_id))
        except ValueError:
            return {"success": False, "error": "Invalid zone_id"}

        zone_result = await db.execute(select(Zone).where(Zone.id == zone_uuid))
        zone = zone_result.scalar_one_or_none()
        if not zone:
            return {"success": False, "error": "Zone not found"}

        if overwrite:
            from sqlalchemy import delete

            await db.execute(delete(Schedule).where(Schedule.zone_id == zone_uuid))

        day_map = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }

        created: list[Schedule] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            day_key = str(entry.get("day_of_week", "")).lower()
            if day_key not in day_map:
                continue
            start_time = entry.get("time")
            target_c = entry.get("target_c")
            if start_time is None or target_c is None:
                continue

            try:
                target_c_value = float(target_c)
            except (TypeError, ValueError):
                continue

            target_c_value = max(
                settings.safety_min_temp_c,
                min(settings.safety_max_temp_c, target_c_value),
            )

            hvac_mode = entry.get("mode") or "auto"
            schedule = Schedule(
                name=f"AI schedule {zone.name} {day_key} {start_time}",
                zone_id=zone_uuid,
                days_of_week=[day_map[day_key]],
                start_time=str(start_time),
                end_time=None,
                target_temp_c=target_c_value,
                hvac_mode=str(hvac_mode),
                is_enabled=True,
                priority=1,
            )
            db.add(schedule)
            created.append(schedule)

        if not created:
            return {"success": False, "error": "No valid schedule entries provided"}

        await db.commit()

        return {
            "success": True,
            "created_count": len(created),
            "timezone": timezone,
            "schedule_ids": [str(s.id) for s in created],
        }

    return {"success": False, "error": f"Unknown tool: {func_name}"}


# ============================================================================
# Routes
# ============================================================================


@router.post("", response_model=ChatResponse)
async def send_chat_message(
    payload: ChatMessage,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatResponse:
    """
    Send a message to the AI assistant.

    The assistant can understand natural language requests about
    temperature, scheduling, and zone control.
    """
    settings = get_settings()
    try:
        llm = await get_llm_provider()
    except HTTPException:
        # Fall back to simple response if no LLM configured
        return ChatResponse(
            message="I'm sorry, but I'm not fully configured yet. Please add an LLM API key in settings to enable AI chat.",
            session_id=payload.session_id or str(uuid.uuid4()),
            timestamp=datetime.now(UTC),
        )

    # Generate or use existing session ID
    session_id = payload.session_id or str(uuid.uuid4())

    # Build context
    zone_context = await get_zone_context(db, settings.temperature_unit)
    conditions_context = await get_conditions_context(db, settings.temperature_unit)
    directives_context = await _get_active_directives(db)

    system_prompt = SYSTEM_PROMPT.format(
        logic_reference=_get_logic_reference_text(),
        directives=directives_context,
        system_state=await _get_live_system_context(db, settings.temperature_unit),
        zones=zone_context,
        conditions=conditions_context,
    )

    # Get conversation history for context
    history_result = await db.execute(
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .order_by(desc(Conversation.created_at))
        .limit(10)
    )
    history = history_result.scalars().all()

    # Build messages list
    messages = []
    for conv in reversed(history):
        messages.append({"role": "user", "content": conv.user_message})
        messages.append({"role": "assistant", "content": conv.assistant_response})
    messages.append({"role": "user", "content": payload.message})

    try:
        # Get response from LLM
        response = await llm.chat(
            messages=messages,
            system=system_prompt,
            tools=get_climate_tools(),
        )

        assistant_message = response.get("content", "I'm not sure how to help with that.")
        tool_calls = response.get("tool_calls", [])
        actions_taken: list[dict[str, Any]] = []

        # Execute any tool calls the LLM requested
        for tc in tool_calls:
            func_info = tc.get("function", {})
            func_name = func_info.get("name", "")
            func_args = _parse_tool_args(func_info.get("arguments"))

            tool_result: dict[str, Any] = {"tool": func_name, "args": func_args}
            try:
                tool_result.update(await _execute_tool_call(func_name, func_args, db))
            except Exception as tool_exc:
                tool_result["error"] = str(tool_exc)
            actions_taken.append(tool_result)

    except Exception as e:
        assistant_message = f"I encountered an error: {e!s}. Please try again."
        actions_taken = []

    # Save conversation
    conversation = Conversation(
        session_id=session_id,
        user_message=payload.message,
        assistant_response=assistant_message,
        metadata_={
            "actions": actions_taken,
            "context": payload.context,
        },
    )
    db.add(conversation)
    await db.commit()

    # Extract directives from the conversation (fire-and-forget)
    zones_for_extraction = list(
        (await db.execute(select(Zone).where(Zone.is_active.is_(True)))).scalars().all()
    )
    try:
        await _extract_directives(
            user_message=payload.message,
            assistant_response=assistant_message,
            conversation_id=conversation.id,
            db=db,
            zones=zones_for_extraction,
        )
    except Exception as extract_err:
        logger.debug("Directive extraction error (non-critical): %s", extract_err)

    # Generate contextual suggestions based on zones and time
    suggestions = _generate_suggestions(zones_list=zones_for_extraction)

    return ChatResponse(
        message=assistant_message,
        session_id=session_id,
        actions_taken=actions_taken,
        suggestions=suggestions,
        metadata={"conversation_id": str(conversation.id)},
        timestamp=datetime.now(UTC),
    )


@router.get("/history", response_model=list[ConversationHistoryItem])
async def get_chat_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    session_id: str | None = None,
    limit: int = 50,
) -> list[ConversationHistoryItem]:
    """
    Get conversation history.

    Optionally filter by session_id to get a specific conversation thread.
    """
    stmt = select(Conversation).order_by(desc(Conversation.created_at)).limit(limit)

    if session_id:
        stmt = stmt.where(Conversation.session_id == session_id)

    result = await db.execute(stmt)
    conversations = result.scalars().all()

    return [ConversationHistoryItem.model_validate(c) for c in conversations]


@router.delete("/history/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Clear conversation history for a session."""
    from sqlalchemy import delete

    await db.execute(delete(Conversation).where(Conversation.session_id == session_id))
    await db.commit()


@router.post("/command", response_model=CommandResponse)
async def execute_command(
    payload: CommandRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommandResponse:
    """
    Execute a voice or text command.

    This is a simplified interface for quick commands like:
    - "Set living room to 72"
    - "Turn off bedroom AC"
    - "Make it warmer"
    """
    settings = get_settings()

    # Get available zones
    result = await db.execute(select(Zone).where(Zone.is_active.is_(True)))
    zones = result.scalars().all()

    if not zones:
        return CommandResponse(
            success=False,
            message="No zones are configured. Please set up zones first.",
        )

    # Parse the command
    parsed = await parse_command(payload.command, list(zones), settings.temperature_unit)

    if not parsed:
        # Fall back to AI parsing
        try:
            llm = await get_llm_provider()
            response = await llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": f"Parse this HVAC command and return JSON with action, zone, and value: {payload.command}",
                    }
                ],
                system="You are a command parser. Return only valid JSON.",
            )
            # Try to parse the response as JSON
            import json

            try:
                parsed = json.loads(response.get("content", "{}"))
            except json.JSONDecodeError:
                parsed = None
        except Exception:
            parsed = None

    parsed = _normalize_parsed_command(parsed, list(zones), settings.temperature_unit)

    if parsed and payload.zone_id and not parsed.get("zone_id"):
        parsed["zone_id"] = payload.zone_id
    if parsed and parsed.get("zone_id") and not parsed.get("zone_name"):
        match = next((zone for zone in zones if zone.id == parsed.get("zone_id")), None)
        if match:
            parsed["zone_name"] = match.name

    if not parsed or not isinstance(parsed, dict):
        return CommandResponse(
            success=False,
            message=f"I couldn't understand the command: {payload.command}. Try something like 'Set living room to 72 degrees'.",
        )

    # Execute the action
    action = parsed.get("action")
    zone_name = parsed.get("zone_name")

    if action == "set_temperature":
        temp_c = _get_temp_c(parsed, settings.temperature_unit)
        zone_id = parsed.get("zone_id")

        if not zone_id:
            return CommandResponse(
                success=False,
                message="No zone specified for temperature command.",
                action="set_temperature",
            )

        # Actually execute the temperature change via HA
        try:
            from backend.integrations.ha_client import HAClient

            # Safety clamp temperature to absolute bounds
            if temp_c is not None:
                temp_c = max(
                    settings.safety_min_temp_c,
                    min(settings.safety_max_temp_c, float(temp_c)),
                )

            if settings.home_assistant_token and temp_c is not None:
                # Find devices in this zone
                from backend.models.database import Device

                device_result = await db.execute(select(Device).where(Device.zone_id == zone_id))
                devices = device_result.scalars().all()
                climate_device = next(
                    (d for d in devices if d.ha_entity_id and d.type.value == "thermostat"),
                    None,
                )
                if climate_device and climate_device.ha_entity_id:
                    async with HAClient(
                        str(settings.home_assistant_url), settings.home_assistant_token
                    ) as ha:
                        await ha.set_temperature(climate_device.ha_entity_id, float(temp_c))
        except Exception as exc:
            logger.exception("Failed to set temperature via HA")
            return CommandResponse(
                success=False,
                message=f"Failed to set temperature: {exc}",
                action="set_temperature",
                zone_affected=zone_name,
            )

        if temp_c is None:
            return CommandResponse(
                success=False,
                message="Temperature value missing or invalid.",
                action="set_temperature",
                zone_affected=zone_name,
            )

        display_temp, display_unit = _format_temp_for_display(temp_c, settings.temperature_unit)
        return CommandResponse(
            success=True,
            message=f"Setting {zone_name} to {display_temp:.1f}\u00b0{display_unit}",
            action="set_temperature",
            zone_affected=zone_name,
            new_value=display_temp,
        )

    elif action == "adjust_temperature":
        direction = parsed.get("direction")
        amount_c = parsed.get("amount_c", 2)
        zone_id = parsed.get("zone_id")

        if not zone_id:
            return CommandResponse(
                success=False,
                message="No zone specified for temperature adjustment.",
                action="adjust_temperature",
            )

        new_temp: float | None = None
        try:
            from backend.config import get_settings as _get_settings
            from backend.integrations.ha_client import HAClient
            from backend.models.database import Device, Sensor, SensorReading

            # Get current temperature from latest sensor reading
            reading_stmt = (
                select(SensorReading)
                .join(Sensor, Sensor.id == SensorReading.sensor_id)
                .where(Sensor.zone_id == zone_id)
                .order_by(SensorReading.recorded_at.desc())
                .limit(1)
            )
            reading_result = await db.execute(reading_stmt)
            reading = reading_result.scalar_one_or_none()
            current_temp_c = (
                reading.temperature_c if reading and reading.temperature_c is not None else 21.0
            )

            if direction == "warmer":
                new_temp = current_temp_c + float(amount_c)
            else:
                new_temp = current_temp_c - float(amount_c)

            settings = _get_settings()

            # Safety clamp to absolute bounds
            new_temp = max(settings.safety_min_temp_c, min(settings.safety_max_temp_c, new_temp))
            if settings.home_assistant_token and zone_id:
                device_result = await db.execute(select(Device).where(Device.zone_id == zone_id))
                devices = device_result.scalars().all()
                climate_device = next(
                    (d for d in devices if d.ha_entity_id and d.type.value == "thermostat"),
                    None,
                )
                if climate_device and climate_device.ha_entity_id:
                    async with HAClient(
                        str(settings.home_assistant_url), settings.home_assistant_token
                    ) as ha:
                        await ha.set_temperature(climate_device.ha_entity_id, new_temp)
        except Exception as exc:
            logger.exception("Failed to adjust temperature via HA")
            return CommandResponse(
                success=False,
                message=f"Failed to adjust temperature: {exc}",
                action="adjust_temperature",
                zone_affected=zone_name,
            )

        display_amount, display_unit = _format_delta_for_display(
            float(amount_c), settings.temperature_unit
        )
        return CommandResponse(
            success=True,
            message=f"Making {zone_name} {direction} by {display_amount:.1f}\u00b0{display_unit}",
            action="adjust_temperature",
            zone_affected=zone_name,
            new_value=f"{'+' if direction == 'warmer' else '-'}{display_amount:.1f}",
        )

    elif action == "toggle_zone":
        enabled = parsed.get("enabled")
        zone_id = parsed.get("zone_id")

        if not zone_id:
            return CommandResponse(
                success=False,
                message="No zone specified to toggle.",
                action="toggle_zone",
            )

        try:
            from backend.config import get_settings as _get_settings
            from backend.integrations.ha_client import HAClient
            from backend.models.database import Device

            settings = _get_settings()
            if settings.home_assistant_token and zone_id:
                device_result = await db.execute(select(Device).where(Device.zone_id == zone_id))
                devices = device_result.scalars().all()
                async with HAClient(
                    str(settings.home_assistant_url), settings.home_assistant_token
                ) as ha:
                    for dev in devices:
                        if dev.ha_entity_id:
                            if enabled:
                                await ha.turn_on(dev.ha_entity_id)
                            else:
                                await ha.turn_off(dev.ha_entity_id)
        except Exception as exc:
            logger.exception("Failed to toggle zone via HA")
            return CommandResponse(
                success=False,
                message=f"Failed to toggle zone: {exc}",
                action="toggle_zone",
                zone_affected=zone_name,
            )

        return CommandResponse(
            success=True,
            message=f"Turning {zone_name} {'on' if enabled else 'off'}",
            action="toggle_zone",
            zone_affected=zone_name,
            new_value=enabled,
        )

    elif action == "query_temperature":
        zone_id = parsed.get("zone_id")
        if not zone_id:
            return CommandResponse(
                success=False,
                message="No zone specified for temperature query.",
            )
        from backend.models.database import Sensor, SensorReading

        reading_stmt = (
            select(SensorReading)
            .join(Sensor, Sensor.id == SensorReading.sensor_id)
            .where(Sensor.zone_id == zone_id)
            .order_by(SensorReading.recorded_at.desc())
            .limit(1)
        )
        reading_result = await db.execute(reading_stmt)
        reading = reading_result.scalar_one_or_none()
        if not reading or reading.temperature_c is None:
            return CommandResponse(
                success=False,
                message=f"No recent temperature data for {zone_name}.",
                action="query_temperature",
                zone_affected=zone_name,
            )
        temp_c = reading.temperature_c
        display_temp, display_unit = _format_temp_for_display(temp_c, settings.temperature_unit)
        return CommandResponse(
            success=True,
            message=f"The temperature in {zone_name} is {display_temp:.1f}\u00b0{display_unit}",
            action="query_temperature",
            zone_affected=zone_name,
            new_value=display_temp,
        )

    return CommandResponse(
        success=False,
        message="I understood the command but couldn't execute it.",
    )


@router.websocket("/ws")
async def chat_websocket(
    websocket: WebSocket,
) -> None:
    """
    WebSocket endpoint for real-time chat.

    Uses short-lived DB sessions per message instead of holding one open
    for the entire WebSocket lifetime.

    Message format:
    {
        "type": "message" | "ping",
        "content": "user message",
        "session_id": "optional session id"
    }

    Response format:
    {
        "type": "response" | "typing" | "error",
        "content": "assistant response",
        "session_id": "session id",
        "actions": [],
        "timestamp": "ISO timestamp"
    }
    """
    from backend.models.database import get_session_maker

    await websocket.accept()
    session_id = str(uuid.uuid4())

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": session_id,
                "message": "Hello! I'm ClimateIQ, your HVAC assistant. How can I help you?",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        while True:
            data = await websocket.receive_json()

            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if data.get("type") != "message":
                continue

            # Use provided session_id if available
            if data.get("session_id"):
                session_id = data["session_id"]

            user_message = data.get("content", "")

            # Send typing indicator
            await websocket.send_json(
                {
                    "type": "typing",
                    "session_id": session_id,
                }
            )

            # Process message using a short-lived DB session
            try:
                llm = await get_llm_provider()

                session_maker = get_session_maker()
                async with session_maker() as db:
                    settings = get_settings()
                    zone_context = await get_zone_context(db, settings.temperature_unit)
                    conditions_context = await get_conditions_context(
                        db,
                        settings.temperature_unit,
                    )

                    directives_ctx = await _get_active_directives(db)

                    response = await llm.chat(
                        messages=[{"role": "user", "content": user_message}],
                        system=SYSTEM_PROMPT.format(
                            logic_reference=_get_logic_reference_text(),
                            directives=directives_ctx,
                            system_state=await _get_live_system_context(
                                db, settings.temperature_unit
                            ),
                            zones=zone_context,
                            conditions=conditions_context,
                        ),
                        tools=get_climate_tools(),
                    )

                    assistant_message = response.get(
                        "content", "I'm not sure how to help with that."
                    )
                    tool_calls = response.get("tool_calls", [])
                    actions_taken: list[dict[str, Any]] = []

                    for tc in tool_calls:
                        func_info = tc.get("function", {})
                        func_name = func_info.get("name", "")
                        func_args = _parse_tool_args(func_info.get("arguments"))
                        tool_result: dict[str, Any] = {"tool": func_name, "args": func_args}
                        try:
                            tool_result.update(await _execute_tool_call(func_name, func_args, db))
                        except Exception as tool_exc:
                            tool_result["error"] = str(tool_exc)
                        actions_taken.append(tool_result)

                    conversation_ws = Conversation(
                        session_id=session_id,
                        user_message=user_message,
                        assistant_response=assistant_message,
                        metadata_={"via": "websocket", "actions": actions_taken},
                    )
                    db.add(conversation_ws)
                    await db.commit()

                actions = actions_taken

            except Exception as e:
                assistant_message = f"I encountered an error: {e!s}"
                actions = []

            # Send response
            await websocket.send_json(
                {
                    "type": "response",
                    "content": assistant_message,
                    "session_id": session_id,
                    "actions": actions,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
        except Exception:
            return


# ============================================================================
# Directive / Memory Endpoints
# ============================================================================


class DirectiveResponse(BaseModel):
    """A user directive / preference."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    directive: str
    source_conversation_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    category: str = "preference"
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class DirectiveCreate(BaseModel):
    """Create a directive manually."""

    directive: str = Field(..., min_length=1, max_length=2000)
    zone_id: uuid.UUID | None = None
    category: str = "preference"


@router.get("/directives", response_model=list[DirectiveResponse])
async def list_directives(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_only: bool = True,
) -> list[DirectiveResponse]:
    """List all user directives / memory items."""
    from backend.models.database import UserDirective

    stmt = select(UserDirective).order_by(desc(UserDirective.created_at))
    if active_only:
        stmt = stmt.where(UserDirective.is_active.is_(True))

    result = await db.execute(stmt)
    return [DirectiveResponse.model_validate(d) for d in result.scalars().all()]


@router.post("/directives", response_model=DirectiveResponse, status_code=status.HTTP_201_CREATED)
async def create_directive(
    payload: DirectiveCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DirectiveResponse:
    """Create a user directive manually."""
    from backend.models.database import UserDirective

    directive = UserDirective(
        directive=payload.directive,
        zone_id=payload.zone_id,
        category=payload.category,
    )
    db.add(directive)
    await db.commit()
    await db.refresh(directive)
    return DirectiveResponse.model_validate(directive)


@router.delete("/directives/{directive_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_directive(
    directive_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete (deactivate) a user directive."""
    from backend.models.database import UserDirective

    result = await db.execute(
        select(UserDirective).where(UserDirective.id == directive_id)
    )
    directive = result.scalar_one_or_none()
    if not directive:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Directive not found")

    directive.is_active = False
    await db.commit()
