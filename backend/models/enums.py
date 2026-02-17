"""Domain enums for ClimateIQ database models."""

from enum import StrEnum


class ZoneType(StrEnum):
    bedroom = "bedroom"
    living_area = "living_area"
    kitchen = "kitchen"
    bathroom = "bathroom"
    hallway = "hallway"
    basement = "basement"
    attic = "attic"
    garage = "garage"
    office = "office"
    other = "other"


class SensorType(StrEnum):
    multisensor = "multisensor"
    temp_only = "temp_only"
    humidity_only = "humidity_only"
    presence_only = "presence_only"
    temp_humidity = "temp_humidity"
    presence_lux = "presence_lux"
    other = "other"


class DeviceType(StrEnum):
    thermostat = "thermostat"
    smart_vent = "smart_vent"
    blind = "blind"
    shade = "shade"
    space_heater = "space_heater"
    fan = "fan"
    mini_split = "mini_split"
    humidifier = "humidifier"
    dehumidifier = "dehumidifier"
    other = "other"


class ControlMethod(StrEnum):
    ha_service_call = "ha_service_call"


class ActionType(StrEnum):
    set_temperature = "set_temperature"
    set_vent_position = "set_vent_position"
    set_mode = "set_mode"
    open_cover = "open_cover"
    close_cover = "close_cover"
    set_cover_position = "set_cover_position"
    turn_on = "turn_on"
    turn_off = "turn_off"
    set_fan_speed = "set_fan_speed"


class TriggerType(StrEnum):
    schedule = "schedule"
    llm_decision = "llm_decision"
    user_override = "user_override"
    follow_me = "follow_me"
    comfort_correction = "comfort_correction"
    rule_engine = "rule_engine"
    anomaly_response = "anomaly_response"


class SystemMode(StrEnum):
    learn = "learn"
    scheduled = "scheduled"
    follow_me = "follow_me"
    active = "active"


class FeedbackType(StrEnum):
    too_hot = "too_hot"
    too_cold = "too_cold"
    too_humid = "too_humid"
    too_dry = "too_dry"
    comfortable = "comfortable"
    schedule_change = "schedule_change"
    preference = "preference"
    other = "other"


class PatternType(StrEnum):
    weekday = "weekday"
    weekend = "weekend"
    holiday = "holiday"


class Season(StrEnum):
    spring = "spring"
    summer = "summer"
    fall = "fall"
    winter = "winter"
