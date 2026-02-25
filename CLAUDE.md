# ClimateIQ — Claude Context

## Architecture
- **Backend**: Python/FastAPI, async SQLAlchemy (PostgreSQL/TimescaleDB), Redis
- **Frontend**: React 19 + TypeScript, Vite, TailwindCSS 4, TanStack Query, Zustand
- **Integrations**: Home Assistant (HA) for thermostat + sensor control
- Single thermostat (e.g. Ecobee) + per-room Zigbee sensors

## Key Design Rules
- All temperatures stored **internally in Celsius**. Convert to/from HA unit at the boundary. Convert to user display unit (°F or °C, stored in `system_settings.temperature_unit`) for API responses.
- `schedule_target_temp` = what the **schedule wants the zone to be** (the desired room temp)
- `target_temp` on a zone = what the **thermostat is currently set to** (may include offset compensation)
- These are different numbers — NEVER show the thermostat setpoint as the "schedule target"

## Temperature Display — Dashboard Rules
- **"Avg Temp" card**: `stats.avgTemp` = zone sensor readings (current room temps). "Set:" label = `overrideStatus.schedule_target_temp` (NOT zone.targetTemperature avg)
- **"Target Temperature" in Manual Override**: `manualTemp` (user's chosen value) or falls back to `overrideStatus.schedule_target_temp`
- **Status bar "Current Temp"**: `overrideStatus.schedule_avg_temp` = avg live sensor reading of schedule zones
- **Status bar "All Zones"**: `overrideStatus.all_zones_avg_temp` = avg live sensor reading of all active zones
- **Status bar "Thermostat"**: `overrideStatus.current_temp` = what the thermostat hardware reads

## Sensor Reading for Offset Compensation (`temp_compensation.py`)
- `_get_live_zone_temp_c()` reads from HA via `sensor.ha_entity_id`
- Filter: accepts `device_class == "temperature"` OR entities with temp unit (°F/°C) and no device_class (handles multisensors)
- If ALL sensors return `None`, `get_avg_zone_temp_c()` returns `(None, None)` → Dashboard shows `--`

## Common Pitfalls
- Zigbee multisensors often lack `device_class` on the entity — don't filter strictly on `device_class == "temperature"` alone
- `zone.target_temp` in the zones API = global thermostat setpoint (offset-adjusted), NOT the schedule's desired zone temp
- Ecobee uses `target_temp_low` (heat) and `target_temp_high` (cool) — `temperature` attr may be absent

## Pre-Commit Checks (REQUIRED before every commit)
Run these before committing any change. Fix all errors — do not commit with failures.

```bash
# Backend — lint
cd /Users/joshuaseidel/climateIQ && ruff check backend/

# Frontend — type-check + build (catches TS errors the same way Docker does)
cd /Users/joshuaseidel/climateIQ/frontend && npm run typecheck && npm run build
```

Both must pass clean. A green local build = no Docker build surprises.

## Version Bump Checklist
When bumping the version, ALL of these files must be updated:
- `VERSION` (used by `addon.yml` to tag Docker images — **most critical**)
- `climateiq/config.yaml`
- `backend/pyproject.toml`
- `frontend/package.json`
- `climateiq/CHANGELOG.md`

## File Map
- `frontend/src/pages/Dashboard.tsx` — main dashboard UI
- `backend/api/routes/system.py:1275` — `get_override_status()` endpoint
- `backend/api/routes/zones.py` — zone list + `_enrich_zone_response()`
- `backend/core/temp_compensation.py` — offset computation + live sensor reads
- `frontend/src/types/index.ts` — `OverrideStatus`, `Zone`, etc.
