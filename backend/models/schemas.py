"""Pydantic schemas for ClimateIQ models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    ActionType,
    ControlMethod,
    DeviceType,
    FeedbackType,
    PatternType,
    Season,
    SensorType,
    SystemMode,
    TriggerType,
    ZoneType,
)

_NUMERIC_PREF_KEYS = ("temp_min", "temp_max", "lux_max", "lux_open", "target_humidity")


def _check_comfort_preferences(v: dict[str, Any] | None) -> dict[str, Any] | None:
    """Numeric preference fields must be finite numbers and temp_min <= temp_max."""
    import math

    if v is None:
        return v
    nums: dict[str, float] = {}
    for key in _NUMERIC_PREF_KEYS:
        if v.get(key) is None:
            continue
        try:
            x = float(v[key])
        except (TypeError, ValueError):
            raise ValueError(f"comfort_preferences.{key} must be a number") from None
        if not math.isfinite(x):
            raise ValueError(f"comfort_preferences.{key} must be finite")
        nums[key] = x
    for key in ("temp_min", "temp_max"):
        # Stored in °C: catches °F values (68/75) sent as °C.
        if key in nums and not -10.0 <= nums[key] <= 50.0:
            raise ValueError(f"comfort_preferences.{key} must be a Celsius value (-10..50)")
    if "temp_min" in nums and "temp_max" in nums and nums["temp_min"] > nums["temp_max"]:
        raise ValueError("comfort_preferences.temp_min must not exceed temp_max")
    return v


_PROFILE_NUMERIC = (
    "heating_rate_c_per_hour", "cooling_rate_c_per_hour", "response_lag_minutes",
    "typical_overshoot_c", "data_days",
)


def _check_thermal_profile(v: dict[str, Any] | None) -> dict[str, Any] | None:
    import math

    if v is None:
        return v
    for key in _PROFILE_NUMERIC:
        x = v.get(key)
        if x is None:
            continue
        if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x):
            raise ValueError(f"thermal_profile.{key} must be a finite number")
    occ = v.get("occupancy_score_by_hour")
    if occ is not None and (
        not isinstance(occ, dict)
        or any(isinstance(x, bool) or not isinstance(x, int | float) for x in occ.values())
    ):
        raise ValueError("thermal_profile.occupancy_score_by_hour must map hour -> number")
    nap = v.get("typical_nap_hours")
    if nap is not None and not isinstance(nap, list):
        raise ValueError("thermal_profile.typical_nap_hours must be a list")
    return v


def _check_exclude_months(v: list[int] | None) -> list[int] | None:
    if v is not None and any(not 1 <= m <= 12 for m in v):
        raise ValueError("exclude_months entries must be 1-12")
    return v


class ZoneBase(BaseModel):
    name: str
    description: str | None = None
    type: ZoneType
    floor: int | None = None
    is_active: bool = True
    priority: int = Field(default=5, ge=1, le=10, description="Zone priority (1-10, higher = more important)")
    comfort_preferences: dict[str, Any] = Field(default_factory=dict)
    thermal_profile: dict[str, Any] = Field(default_factory=dict)
    exclude_from_metrics: bool = False
    exclude_months: list[int] = Field(
        default_factory=list,
        description=(
            "Calendar months (1-12) during which the zone is excluded. "
            "Empty list means always excluded when exclude_from_metrics is true."
        ),
    )
    ha_entities: list[str] = Field(
        default_factory=list,
        description=(
            "Open-ended list of Home Assistant entity IDs attached to this "
            "zone (motion sensors, door contacts, lights, switches, plugs, "
            "etc.) that feed extra occupancy signal to the AI."
        ),
    )
    fan_entities: list[str] = Field(
        default_factory=list,
        description=(
            "Home Assistant fan entity IDs in this zone.  Active fans widen "
            "the AI's comfort tolerance because airflow makes a room feel "
            "cooler than its measured temperature."
        ),
    )
    allow_fan_control: bool = Field(
        default=False,
        description=(
            "When True, Active mode is allowed to actively drive fan speeds "
            "in this zone as a cooling/heating helper.  Always disabled at "
            "runtime for zones with exclude_from_metrics=True.  System will "
            "never lower the fan below its speed at takeover time."
        ),
    )


class ZoneCreate(ZoneBase):
    model_config = ConfigDict(extra="forbid")

    # Input-only: ZoneResponse (also a ZoneBase) must never re-validate stored rows.
    @field_validator("comfort_preferences")
    @classmethod
    def _v_prefs(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _check_comfort_preferences(v)

    @field_validator("exclude_months")
    @classmethod
    def _v_months(cls, v: list[int] | None) -> list[int] | None:
        return _check_exclude_months(v)

    @field_validator("thermal_profile")
    @classmethod
    def _v_profile(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _check_thermal_profile(v)


class ZoneUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    type: ZoneType | None = None
    floor: int | None = None
    is_active: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=10)
    comfort_preferences: dict[str, Any] | None = None
    thermal_profile: dict[str, Any] | None = None
    exclude_from_metrics: bool | None = None
    exclude_months: list[int] | None = None
    ha_entities: list[str] | None = None
    fan_entities: list[str] | None = None
    allow_fan_control: bool | None = None

    @field_validator("comfort_preferences")
    @classmethod
    def _v_prefs(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _check_comfort_preferences(v)

    @field_validator("exclude_months")
    @classmethod
    def _v_months(cls, v: list[int] | None) -> list[int] | None:
        return _check_exclude_months(v)

    @field_validator("thermal_profile")
    @classmethod
    def _v_profile(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _check_thermal_profile(v)


class SensorBase(BaseModel):
    name: str
    type: SensorType
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    ha_entity_id: str | None = None
    entity_id: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    calibration_offsets: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    last_seen: datetime | None = None


class SensorCreate(SensorBase):
    zone_id: uuid.UUID


class SensorUpdate(BaseModel):
    name: str | None = None
    type: SensorType | None = None
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    config: dict[str, Any] | None = None
    ha_entity_id: str | None = None
    entity_id: str | None = None
    capabilities: dict[str, Any] | None = None
    calibration_offsets: dict[str, Any] | None = None
    is_active: bool | None = None
    last_seen: datetime | None = None


class DeviceBase(BaseModel):
    name: str
    type: DeviceType
    manufacturer: str | None = None
    model: str | None = None
    ha_entity_id: str | None = None
    control_method: ControlMethod
    capabilities: dict[str, Any] = Field(default_factory=dict)
    is_primary: bool = False
    constraints: dict[str, Any] = Field(default_factory=dict)


class DeviceCreate(DeviceBase):
    zone_id: uuid.UUID


class DeviceUpdate(BaseModel):
    name: str | None = None
    type: DeviceType | None = None
    manufacturer: str | None = None
    model: str | None = None
    ha_entity_id: str | None = None
    control_method: ControlMethod | None = None
    capabilities: dict[str, Any] | None = None
    is_primary: bool | None = None
    constraints: dict[str, Any] | None = None


class SensorResponse(SensorBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID
    created_at: datetime


class DeviceResponse(DeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID
    created_at: datetime


class ZoneResponse(ZoneBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    sensors: list[SensorResponse] = Field(default_factory=list)
    devices: list[DeviceResponse] = Field(default_factory=list)

    # Live sensor data (enriched by the API layer)
    current_temp: float | None = None
    current_humidity: float | None = None
    current_lux: float | None = None
    is_occupied: bool | None = None
    target_temp: float | None = None

    # Computed: whether the zone is currently excluded right now
    is_currently_excluded: bool = False


class SensorReadingCreate(BaseModel):
    sensor_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    recorded_at: datetime | None = None
    temperature_c: float | None = None
    humidity: float | None = None
    presence: bool | None = None
    lux: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SensorReadingResponse(SensorReadingCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recorded_at: datetime | None = None


class DeviceActionCreate(BaseModel):
    device_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    triggered_by: TriggerType
    action_type: ActionType
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    reasoning: str | None = None
    mode: SystemMode | None = None


class DeviceActionResponse(DeviceActionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class SystemConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    current_mode: SystemMode
    default_schedule: dict[str, Any] | None = None
    llm_settings: dict[str, Any] = Field(default_factory=dict)
    last_synced_at: datetime | None = None


class OccupancyPatternResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID
    pattern_type: PatternType
    season: Season
    schedule: list[dict[str, Any]]
    confidence: float | None
    created_at: datetime


class UserFeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID | None
    feedback_type: FeedbackType
    comment: str | None
    embedding: list[float] | None = None
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> UserFeedbackResponse:
        if hasattr(obj, "metadata_"):
            data = {
                "id": obj.id,
                "zone_id": obj.zone_id,
                "feedback_type": obj.feedback_type,
                "comment": obj.comment,
                "embedding": list(obj.embedding) if obj.embedding is not None else None,
                "metadata": obj.metadata_ if obj.metadata_ is not None else {},
                "created_at": obj.created_at,
            }
            return super().model_validate(data, **kwargs)
        return super().model_validate(obj, **kwargs)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: str
    user_message: str
    assistant_response: str
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> ConversationResponse:
        if hasattr(obj, "metadata_"):
            data = {
                "id": obj.id,
                "session_id": obj.session_id,
                "user_message": obj.user_message,
                "assistant_response": obj.assistant_response,
                "metadata": obj.metadata_ if obj.metadata_ is not None else {},
                "created_at": obj.created_at,
            }
            return super().model_validate(data, **kwargs)
        return super().model_validate(obj, **kwargs)


class UserFeedbackCreate(BaseModel):
    zone_id: uuid.UUID | None = None
    feedback_type: FeedbackType
    comment: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationCreate(BaseModel):
    session_id: str
    user_message: str
    assistant_response: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserDirectiveResponse(BaseModel):
    """A user directive / preference extracted from chat."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    directive: str
    source_conversation_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    category: str = "preference"
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class UserDirectiveCreate(BaseModel):
    """Create a user directive manually."""

    directive: str = Field(..., min_length=1, max_length=2000)
    zone_id: uuid.UUID | None = None
    category: str = Field(default="preference", pattern=r"^(preference|constraint|schedule_hint|comfort|energy)$")


class WebSocketMessage(BaseModel):
    message_type: str
    payload: dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
