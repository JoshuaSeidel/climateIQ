"""Pydantic schemas for ClimateIQ models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


class ZoneCreate(ZoneBase):
    model_config = ConfigDict(extra="forbid")


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
