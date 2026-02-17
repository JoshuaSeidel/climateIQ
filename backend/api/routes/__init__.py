"""API route registration for ClimateIQ."""

from fastapi import APIRouter

from . import analytics, backup, chat, devices, schedule, sensors, settings, system, weather, zones

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(zones.router, prefix="/zones", tags=["zones"])
api_router.include_router(sensors.router, prefix="/sensors", tags=["sensors"])
api_router.include_router(devices.router, prefix="/devices", tags=["devices"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(schedule.router, prefix="/schedules", tags=["schedules"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(weather.router, prefix="/weather", tags=["weather"])
api_router.include_router(backup.router, prefix="/backup", tags=["backup"])


__all__ = [
    "analytics",
    "api_router",
    "backup",
    "chat",
    "devices",
    "schedule",
    "sensors",
    "settings",
    "system",
    "weather",
    "zones",
]
