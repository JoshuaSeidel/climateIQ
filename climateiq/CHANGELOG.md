# Changelog

## [0.9.1] - 2026-02-24

### Fixed
- **Chat page crash when AI uses tools**: `ChatMessage.actions` was typed as OpenAI tool-call format (`{ function: { name } }`) but the backend sends action results shaped as `{ tool, args, ... }`. Accessing `action.function.name` on a non-empty `actions_taken` list threw a `TypeError` that crashed the React chat UI. Updated `ChatAction` type to match the backend shape and render `action.tool` instead.

## [0.9.0] - 2026-02-24

### Security
- **Prompt injection hardening** (CRITICAL): LLM-extracted user directives are now capped at 200 characters and XML-escaped before being inserted into the system prompt. The directive block is wrapped in `<user_directives>` tags with an explicit data-only comment so the model treats it as preferences rather than instructions.
- **Backup file size limit** (CRITICAL): `POST /backup/import` now rejects files larger than 50 MB with a 413 response before parsing JSON, preventing unbounded memory allocation.
- **Generic LLM error messages** (HIGH): Raw exception details (which can contain API key fragments and internal URLs) are no longer returned to chat clients. Errors are logged server-side and a generic retry message is shown to the user.
- **WebSocket authentication** (HIGH): `/ws` and `/ws/zones` now check the `api_key` query parameter when an API key is configured, closing unauthenticated connections with code 4001 before accepting them.
- **Backup ID validated as UUID** (HIGH): `DELETE /backup/{backup_id}` now accepts only valid UUID values — FastAPI rejects non-UUID inputs with 422 before the handler runs.
- **X-Request-ID sanitization** (HIGH): Client-supplied `X-Request-ID` headers are validated against a safe alphanumeric pattern before being echoed in responses and logs. Malformed values are replaced with a fresh UUID.
- **API keys removed from config property** (HIGH): `Settings.llm_provider_config` no longer includes `api_key` fields, preventing accidental serialisation of credentials if the property is ever returned from a route.
- **Standalone mode authentication warning** (MEDIUM): Starting the server in non-add-on mode without an `api_key` configured now logs a prominent `WARNING` explaining all endpoints are publicly accessible.
- **HA service call payloads downgraded to DEBUG** (MEDIUM): `ha_client.call_service` previously logged full entity ID + temperature payloads at INFO level. Downgraded to DEBUG to reduce operational data in production logs.

### Fixed
- **None guard in offset compensation**: `apply_offset_compensation` now returns immediately if `desired_temp_c` is `None` rather than crashing with a `TypeError` deep in the formula.
- **Zone delete FK conflict** (MEDIUM): `DELETE /zones/{id}` now returns HTTP 409 Conflict with an actionable message instead of a 500 Internal Server Error when the zone is still referenced by foreign-key constrained rows.
- **Redis fallback connection leak**: The per-request Redis client created in the `get_redis` fallback path is now properly closed in a `finally` block, preventing connection pool exhaustion when the shared client is not initialised.

### Dependencies (security updates)
- `aiohttp>=3.11` → `>=3.13.3` — patches 5 CVEs including path traversal (CVE-2025-69226), DoS (CVE-2025-69228), zip bomb (CVE-2025-69223), and HTTP request smuggling (CVE-2025-53643).
- `litellm>=1.42` → `>=1.66` — clears CVE-2025-45809 (SQL injection) and CVE-2025-0330 (Langfuse API key leak).
- `google-generativeai` → `google-genai>=1.0` — Google terminated support for `google-generativeai` on 2025-11-30; migrated to the current GA SDK.
- `openai>=1.58` → `>=2.0` — aligns with the current major SDK version; v1 receives only maintenance updates.
- `fastapi>=0.111` → `>=0.115`, `uvicorn>=0.30` → `>=0.34`, `alembic>=1.13` → `>=1.15` — floor bumps for quality and bug-fix coverage.
- `ruff>=0.5` → `>=0.15` (dev) — formatter style guide changed significantly at 0.15.

## [0.8.42] - 2026-02-24

### Fixed
- **Zone temperature always resolves for avg/offset**: `get_avg_zone_temp_c` and `get_priority_zone_temp_c` previously only read from HA live sensors. If a sensor entity was unavailable or hadn't been polled yet, the zone was excluded from the average — causing `schedule_avg_temp` to return null and the dashboard to fall back to the all-zones average. Both functions now fall back to the most-recent DB sensor reading (same source as the zone cards), ensuring the schedule-zone avg and offset compensation always have data when sensor readings exist in the database.

## [0.8.41] - 2026-02-24

### Fixed
- **Offset compensation clamp**: When a zone is already above the schedule target in heat mode, the offset formula was computing a setpoint *below* the schedule target (e.g. 66°F when the target is 69°F and Oliver's room reads 72°F). This caused the thermostat to sit too low — the HVAC would only restart once the thermostat location dropped below that under-target setpoint, risking under-heating. The adjusted setpoint is now floored at the schedule target in heat/heat_cool mode and ceilinged at the target in cool mode. The thermostat correctly holds at 69°F and does not heat (room temp already exceeds setpoint), then resumes heating naturally once the room cools below target.

## [0.8.40] - 2026-02-24

### Fixed
- **Dashboard Avg Temp card**: Was showing the average across all zones regardless of which schedule is active. Now prefers `schedule_avg_temp` (the avg of only the schedule's targeted zones) when a schedule is active. For example, when "Oliver's Bed" activates with only Oliver's room targeted, the card now shows Oliver's room temp instead of the whole-house average.
- **Zone card Target temp**: Was showing the thermostat's hardware setpoint (offset-adjusted) as the "Target" label on every zone card. Now shows the active schedule's desired temperature for zones that are part of the current schedule — the value the system is actually trying to achieve in that room.

## [0.8.39] - 2026-02-24

### Fixed
- **Chat zone temperature fallback**: The HA live-sensor fallback in `get_zone_context` was calling `float(state.state)` on any non-unavailable sensor entity without checking `device_class`. Zigbee multisensors expose multiple entities (lux, battery%, humidity, etc.) — these numeric values were silently treated as °C zone temperatures, causing the LLM to report impossible readings like 93.2°F or 134.6°F. The fallback now only accepts entities with `device_class == "temperature"` or `unit_of_measurement` of `°F`/`°C` with no device_class (matching the pattern used elsewhere for multisensors).

## [0.8.38] - 2026-02-24

### Fixed
- **Chat LLM hallucination**: The AI assistant was inventing temperature readings (e.g. 96°F) for zones that had no sensor data (shown as "awaiting sensor data" in context). Added explicit grounding rules to the system prompt: the LLM must never invent, estimate, or infer sensor values not explicitly present in the context data, and must tell the user when data is unavailable rather than fabricating it.


## [0.8.37] - 2026-02-24

### Fixed
- **Thermostat drift detection**: Temperature unit was read from entity attributes (often absent on Ecobee climate entities), defaulting to °C and misinterpreting 64°F setpoints as 64°C — causing massive false drift and constant correction loops. Now uses `settings_instance.temperature_unit` (the system setting) for correct unit conversion.
- **Hold preservation**: `ecobee.resume_program` was called with `resume_all=True`, which cancelled all holds including ClimateIQ's own temperature hold. Changed to `resume_all=False` to cancel only the current comfort-preset hold, preventing Ecobee from bouncing back to its away schedule (64°F) between resume and the subsequent set_temperature call.

## 0.8.36

### Fixed

- **Immediate thermostat drift correction**: ClimateIQ now reacts in real time
  when the thermostat setpoint is changed externally (e.g. Ecobee away mode
  resetting the heat setpoint to 64°F). A new `_handle_climate_state_change`
  callback is registered on the existing HA WebSocket and fires within 3 seconds
  of any `state_changed` event on the climate entity. If the thermostat's
  current heat setpoint differs from what ClimateIQ last set by more than 1°F
  (one Ecobee step), `maintain_climate_offset` is triggered immediately rather
  than waiting up to 60 seconds for the next scheduled tick. Rapid thermostat
  transitions are debounced so only one correction runs at a time.

## 0.8.35

### Fixed

- **Thermostat set to wrong temperature in heat_cool/auto mode**: In dual-setpoint
  mode (Ecobee "auto"), the old code treated the adjusted setpoint as the
  *midpoint* of the heat/cool spread and subtracted half the spread to get the
  heat setpoint — e.g. target 69°F with a 10°F spread → heat setpoint 64°F.
  The fix: the adjusted setpoint is the heating target and is sent directly as
  `target_temp_low`. The existing cooling setpoint (`target_temp_high`) is kept
  unchanged (only raised if it would fall within 2°F of the heat setpoint to
  satisfy Ecobee's minimum spread requirement).

## 0.8.34

### Fixed

- **Zones disappearing after v0.8.33**: The batched `UNION ALL` raw SQL query
  introduced in v0.8.33 used `ANY(:ids)` with a Python list, which psycopg3
  does not auto-cast to a PostgreSQL array — causing a DB error that made the
  entire `/zones` endpoint return a 500. Reverted to the original 4-query
  approach but now uses `asyncio.gather` to run all four queries concurrently
  rather than sequentially, preserving the performance benefit without the
  type-binding issue.

## 0.8.33

### Performance

- **DB migration 002**: Added five missing indexes — `sensor_readings(sensor_id, recorded_at DESC)`
  compound covering index (the most queried pattern); `sensors(zone_id)` and `devices(zone_id)`
  FK indexes; partial indexes on `zones(is_active)` and `schedules(is_enabled)`.  The
  sensor_readings compound index eliminates full-table scans on the largest table.

- **Zone enrichment batched**: Replaced the 4-queries-per-zone loop in
  `_enrich_zone_response` with a single UNION ALL query that fetches the latest
  non-null value for temperature, humidity, presence, and lux in one round trip.
  Zone list requests now issue O(1) DB reads instead of O(zones × 4).

- **Background task staggering**: All scheduled tasks now have staggered
  `start_date` offsets (0–55 seconds) so they no longer fire simultaneously.
  `execute_schedules` and `maintain_climate_offset` previously both hit the DB
  at exactly T+60s; they now start at T+10s and T+20s respectively.

- **Frontend prefetch on app init**: `AppProviders` now kicks off
  `prefetchQuery` for `settings`, `override-status`, and a zones warm-up
  request the moment the app boots — before the router renders any page.
  React Query deduplicates the in-flight requests so the Dashboard never
  waits for a cold fetch.

## 0.8.32

### Fixed

- **Offset applied in whole-degree increments (°F).** Ecobee thermostats
  move their setpoint in 1°F steps and round at 0.5°F, so a fractional
  offset (e.g. +0.3°F) has no effect — the thermostat sees the same
  setpoint and doesn't turn the heat back on. The zone error is now
  rounded to the nearest whole °F before being applied, guaranteeing
  every non-zero offset actually crosses the thermostat's rounding
  threshold.

## 0.8.31

### Fixed

- **Offset compensation now drives toward zone target instead of tracking
  the thermostat-to-zone gap.** The old formula used
  `desired + (thermostat - zone_avg)` which only compensated for the
  current sensor location gap and would stop heating once the thermostat
  *read* its setpoint — regardless of whether the zones were actually warm
  enough. The new formula uses `desired + (desired - zone_avg)`: the
  thermostat is pushed *above* the target by however much the zones are
  *below* it, so the HVAC keeps running until the zones reach the desired
  temperature. Once zones hit target the offset is zero; if they overshoot,
  the offset goes negative, preventing runaway. The 8°F max-offset cap and
  60-second maintenance loop are unchanged.

- **Status bar now shows thermostat set temp** alongside the reading:
  "Thermostat: 72°F → 73°F" makes the offset-adjusted hardware setpoint
  visible at a glance.

## 0.8.30

### Fixed

- **Target Temp, Current Temp, and All Zones no longer show `--`**. Three
  compounding bugs caused these values to always be null:

  1. **Scope bug** — `_best` (active schedule) was declared inside an inner
     try-block but referenced outside it, causing a silent `NameError` that
     nulled all three values on every request.

  2. **Exception silenced at DEBUG** — failures in the offset computation
     block were logged at DEBUG (invisible in default logs). Now logged at
     WARNING and output variables are pre-declared so a partial failure
     doesn't zero out unrelated fields.

  3. **Sensor reading too strict** — `_get_live_zone_temp_c` rejected sensors
     whose HA entity has a numeric temperature state but no `device_class` or
     `unit_of_measurement`. Now tries three strategies: explicit temp entity,
     plausible numeric state, and temperature stored as an attribute. Also
     checks the sensor's secondary `entity_id` field as a fallback.

  4. **Duplicate schedule-lookup logic** replaced with the shared
     `_get_user_tz` / `parse_time` helpers from the schedule route.

## 0.8.29

### Fixed

- **"Avg Temp" card now shows schedule target temp**, not the thermostat's
  offset-adjusted setpoint. The "Set:" label reflects what ClimateIQ wants
  the rooms to be, not what it sends to the Ecobee hardware.

- **"Current Temp" and "All Zones" status bar values no longer show `--`**.
  Zone sensors (Zigbee multisensors, etc.) that lack a `device_class` attribute
  in HA but have a temperature unit of measurement are now accepted when
  computing live zone averages.

## 0.8.28

### Fixed

- **Target Temperature now shows the schedule's desired temp** instead
  of the thermostat's offset-adjusted setpoint.  The big number in the
  Manual Override card is what ClimateIQ wants the rooms to be, not
  what the thermostat is told to target.

- **Targeting line now shows only active schedule zones** instead of
  the priority zone from offset calculation.  Previously it could show
  zones (e.g. Master Bedroom, Oliver's Room) that weren't part of the
  active schedule.

- **Faster Dashboard polling** -- zone data refreshes every 15s
  (was 30s), override status every 10s (was 15s), active schedule
  every 30s (was 60s) for a more realtime feel.

## 0.8.27

### Fixed

- **Offset compensation now reads live sensor data from Home Assistant**
  instead of querying the sensor_readings database table with a
  30-minute cutoff.  Previously, sensors that hadn't reported a state
  change (because the temperature was stable) would be excluded from
  the zone average, causing the offset to be calculated from only a
  subset of schedule zones.  Now all zone sensors are queried live
  from HA -- only sensors marked unavailable/unknown are skipped.

## 0.8.26

### Fixed

- **Suppressed third-party DEBUG logs at INFO level** -- websockets
  and uvicorn loggers were dumping verbose DEBUG output (connection
  headers, ping/pong, WebSocket frames) even when log level was set
  to `info`.  These loggers are now set to WARNING when not in debug
  mode.

- **Removed preset_mode calls from set_temperature_with_hold** --
  Ecobee automatically creates a temperature hold when
  `set_temperature` is called.  The explicit `set_preset_mode` calls
  with `temp`/`hold` were unnecessary and caused 500 errors.
  `set_temperature_with_hold` now just calls `set_temperature`.

## 0.8.25

### Fixed

- **Offset compensation now uses average of ALL schedule zones** --
  previously used only the highest-priority zone for the offset
  calculation, which could pick a single zone (e.g. Office at 69.8 F)
  that was close to the thermostat, resulting in a tiny offset.  Now
  averages all zones in the active schedule (e.g. Dining Room 68 F,
  Living Room 66 F, Office 70 F, Kitchen 68 F, Foyer 67 F = avg 67.8 F)
  so the offset reflects the true gap between the thermostat and the
  rooms being heated.

- **Silenced Ecobee hold preset errors** -- `set_temperature_with_hold`
  was logging ERROR/WARNING for 500 responses when trying to set
  `temp`/`hold` presets on Ecobee.  Ecobee automatically creates a
  temperature hold on `set_temperature` -- the explicit preset calls
  are unnecessary.  Downgraded to DEBUG level.

### Improved

- **Climate offset maintenance logging at INFO level** -- all key
  decision points now log at INFO level so they appear in the default
  add-on logs.

## 0.8.22

### Added

- **Schedule and all-zones average temperatures on Dashboard** -- the
  Manual Override status bar now shows three temperature readings:
  "Thermostat" (Ecobee hallway sensor), "Current Temp" (average of
  zones in the active schedule), and "All Zones" (average across
  every active zone).  Backend returns `schedule_avg_temp` and
  `all_zones_avg_temp` from the override status endpoint.

- **`get_avg_zone_temp_c()` helper** -- new function in
  `temp_compensation.py` that averages temperatures across all
  matching zones regardless of priority (unlike the existing
  priority-based function used for offset calculation).

## 0.8.21

### Improved

- **Balance temperature across same-priority zones** -- when multiple
  zones in the active schedule share the same highest priority,
  their temperatures are now averaged for offset compensation instead
  of arbitrarily picking one.  For example, if a nighttime schedule
  targets two bedrooms at priority 5, the offset is calculated from
  the average of both rooms so the system heats to balance between
  them.  The Dashboard shows both zone names (e.g. "Targeting Master
  Bedroom, Guest Bedroom").

## 0.8.20

### Fixed

- **Offset compensation now scoped to active schedule's zones** -- the
  Dashboard override status and the climate maintenance loop were
  picking the highest-priority zone globally (e.g. Master Bedroom)
  even when that zone was not part of the currently-active schedule.
  Now both `get_override_status()` and `maintain_climate_offset()` find
  the active schedule and only consider its assigned zones for offset
  compensation.

## 0.8.19

### Added

- **Dedicated climate offset maintenance loop** -- new
  `maintain_climate_offset()` background task runs every 60 seconds,
  independent of schedule firing.  Finds the currently-active schedule,
  re-evaluates offset compensation using live sensor and thermostat
  readings, and updates the thermostat whenever the adjusted setpoint
  drifts by more than 0.5 C.  Skips Follow-Me and Active modes (they
  handle offset in their own loops).  Replaces the v0.8.18
  schedule-window-bound re-eval with a proper continuous control loop.

## 0.8.18

### Fixed

- **Continuous offset re-evaluation for active schedules** -- offset
  compensation was only applied at the moment a schedule fired (within
  a 2-minute window of start_time).  After that, the thermostat held a
  stale setpoint even as zone and hallway temperatures drifted.  Now,
  while a schedule is active (between start_time and end_time), the
  offset is re-evaluated every 60 seconds and the thermostat is updated
  whenever the adjusted setpoint changes by more than 0.5 C.

## 0.8.17

### Fixed

- **Database migration for zones.priority column** -- v0.8.16 added a
  `priority` column to the Zone ORM model but did not include the
  corresponding `ALTER TABLE` migration, causing every query that
  touches the `zones` table to fail with
  `UndefinedColumn: column zones.priority does not exist`.  The startup
  migration now adds the column automatically.

## 0.8.16

### Added

- **Temperature offset compensation** -- ClimateIQ now adjusts the
  target temperature sent to the thermostat to compensate for the
  difference between the thermostat's built-in sensor (e.g. hallway)
  and the priority zone's actual temperature (from Zigbee sensors).
  If the hallway reads 73 F but the bedroom reads 66 F and you want
  69 F in the bedroom, ClimateIQ tells the thermostat to target 76 F.
  Integrated into schedule execution, Follow-Me mode, and Resume
  Schedule.

- **Zone priority (1-10)** -- each zone now has a configurable
  priority.  The highest-priority zone with a recent sensor reading
  is used for offset compensation.  Default is 5.  Editable in the
  Zones page.

- **Max temperature offset setting** -- configurable in Settings
  (default 8 F / 4.4 C).  Caps how much ClimateIQ will adjust the
  thermostat target.  Set to 0 to disable offset compensation.

- **Offset info on Dashboard** -- the Manual Override card now shows
  which zone ClimateIQ is targeting and the current offset when
  compensation is active.

## 0.8.15

### Fixed

- **Stop clearing Ecobee "temp" preset on set_temperature** -- the
  `temp` preset means a temperature hold is already active, which is
  exactly what `set_temperature` creates.  Clearing it via
  `resume_program` snapped back to the Ecobee schedule (e.g. sleep at
  68) and then the subsequent `set_temperature` re-created the hold,
  causing a visible flip-flop between presets.  Now only
  comfort-profile presets (sleep, away, home) are cleared.

- **Override status no longer shows "Override Active" for normal
  temperature holds** -- the `temp` preset is normal ClimateIQ
  operation (we set a temp, Ecobee shows it as a hold).  Only
  comfort-profile presets (sleep, away, home) now trigger the
  "Override Active" badge in the UI.

## 0.8.14

### Fixed

- **Clear thermostat presets before setting temperature** -- when the
  thermostat has an active preset (sleep, away, home, etc.) that holds
  it to a comfort profile, `set_temperature` calls are rejected with
  400. ClimateIQ now detects active presets and clears them
  transparently before sending the temperature command. Tries
  `ecobee.resume_program` first, falls back to setting preset to
  "none".

## 0.8.13

### Changed

- **Enhanced error logging for HA service calls** -- 400/4xx errors
  now log the full response body from HA (up to 500 chars) so we can
  see the exact rejection reason. ``call_service`` also logs the
  complete JSON payload being sent. This will reveal why
  ``set_temperature`` is being rejected.

## 0.8.12

### Fixed

- **set_temperature 400 error -- wrong service parameters** -- the HA
  ``climate.set_temperature`` service uses ``temperature`` for
  single-setpoint modes (heat, cool) and ``target_temp_low`` +
  ``target_temp_high`` only for dual-setpoint modes (heat_cool, auto).
  The v0.8.5 fix incorrectly sent ``target_temp_low`` for heat mode
  and ``target_temp_high`` for cool mode, which HA rejects with 400.
  Now uses ``{"temperature": 69.0}`` for heat/cool/off and only
  switches to the low/high pair for heat_cool/auto. Simplified the
  method to default to ``temperature`` and only override for
  dual-setpoint modes.

## 0.8.11

### Fixed

- **Timezone lookup was importing nonexistent function** --
  ``_get_user_tz()`` tried ``from backend.integrations import
  get_ha_client`` which doesn't exist (``get_ha_client`` is in
  ``backend.api.dependencies``). This ``ImportError`` was silently
  caught, causing the HA config timezone fallback to never execute,
  so the system always fell back to UTC. Fixed to import
  ``_ha_client`` from ``backend.api.dependencies`` directly. This
  was the root cause of all schedule time display issues -- schedule
  times were being treated as UTC instead of the user's local
  timezone.

- **Active schedule still appearing in upcoming list** -- the dedup
  filter only removed the first occurrence of the active schedule
  from the upcoming list, but a second occurrence (next day) remained.
  Now filters out ALL occurrences of the active schedule ID.

## 0.8.10

### Fixed

- **Timezone resolution falling back to UTC** -- ``_get_user_tz()``
  had a fallback path that tried ``Settings.timezone`` which doesn't
  exist, causing a silent ``AttributeError`` that fell through to UTC.
  If the ``system_settings`` DB table has no timezone row, the system
  was treating all schedule times as UTC, shifting them by the user's
  offset (e.g., an 8:00 AM schedule showing as 3:00 AM in EST).
  Now falls back to the HA config ``time_zone`` field (from
  ``/api/config``) before defaulting to UTC. Added debug logging to
  trace which source the timezone came from. Same fix applied to
  ``execute_schedules()`` in ``main.py``.

- **Active schedule shown twice on dashboard** -- the active schedule
  badge and the upcoming schedules list both showed the same schedule.
  The upcoming list now filters out the first occurrence of the
  currently active schedule so it only appears in the green "Now
  Active" badge. Also fixed React duplicate key warnings by using
  index-based keys for schedule occurrences.

## 0.8.9

### Fixed

- **All HA service calls: entity_id was nested under "target"** --
  ``HAClient.call_service()`` was sending
  ``{"target": {"entity_id": "..."}, ...}`` but the HA REST API
  expects ``entity_id`` at the top level of the JSON body. The
  ``target`` nesting is a WebSocket API convention, not REST. This
  caused 400 Bad Request errors on ``climate.set_temperature`` and
  other service calls. Fixed by flattening the target dict into the
  top-level payload. Added diagnostic logging to ``set_temperature``
  showing the exact payload and detected HVAC mode.

## 0.8.8

### Changed

- **Resume Schedule re-applies ClimateIQ schedule** -- the "Resume
  Schedule" button now finds the currently active ClimateIQ schedule
  and re-applies its target temperature to the thermostat, instead of
  trying to resume the Ecobee's own program. ClimateIQ is the control
  system; the thermostat is just an actuator. If no ClimateIQ schedule
  is currently active, the button reports that there is nothing to
  resume. All thermostat-specific hold management (Ecobee vacation
  holds, preset modes) remains an internal implementation detail of
  the temperature-setting methods.

## 0.8.7

### Fixed

- **Upcoming schedule times wrong on dashboard** -- the upcoming
  schedules endpoint mixed UTC and local time when computing
  occurrences, causing wrong times and duplicate entries. Rewrote
  the logic to work entirely in local time: walks each calendar day
  in the window, checks if the schedule fires on that weekday, builds
  local datetimes from the schedule's HH:MM strings, then converts to
  UTC only at the end for the API response. End times are also built
  in local time before conversion, fixing the midnight-wrap case.

- **Resume Schedule button not working** -- the resume quick action
  silently returned success even when all three fallback methods
  failed. Now properly logs each attempt, tries
  ``ecobee.resume_program``, always attempts to delete the
  ``ClimateIQ_Control`` vacation hold, and falls back to setting the
  ``home`` preset. Returns ``success: false`` with details if all
  methods fail. Frontend also now awaits the async action before
  refetching override status.

### Removed

- **Set Temp stat card** -- removed the compact thermostat set-point
  card from the stats grid since the full Manual Override card below
  already provides the same functionality with more control.

## 0.8.6

### Fixed

- **Resume Schedule quick action 400 error** -- the "Resume Schedule"
  button sent ``set_preset_mode`` with ``"none"`` which Ecobee rejects.
  Now tries ``ecobee.resume_program`` first (cancels all holds and
  restores the Ecobee's own schedule), then falls back to the generic
  preset clear for non-Ecobee thermostats, and finally tries deleting
  the ``ClimateIQ_Control`` vacation hold as a last resort.

## 0.8.5

### Fixed

- **Manual override / set_temperature 400 error** -- Ecobee thermostats
  in ``heat`` mode reject the generic ``temperature`` parameter in the
  HA ``climate.set_temperature`` service call, requiring
  ``target_temp_low`` instead (and ``target_temp_high`` for cool mode).
  ``HAClient.set_temperature()`` now reads the entity's current HVAC
  mode and sends the correct parameter: ``target_temp_low`` for heat,
  ``target_temp_high`` for cool, both for auto/heat_cool, or the
  generic ``temperature`` for other modes. This fixes the 400 Bad
  Request errors on manual override, schedule execution, follow-me,
  and active mode temperature changes.

- **Schedule execution crash** -- ``execute_schedules()`` referenced
  ``settings_instance.timezone`` which does not exist on the
  ``Settings`` class, causing an ``AttributeError`` on every schedule
  tick. Fixed to default to UTC and let the DB ``system_settings``
  timezone value take precedence (which was already the next step in
  the code).

## 0.8.4

### Fixed

- **Schedule timezone handling** -- schedule times are stored as local
  HH:MM strings but `execute_schedules()` was comparing them against
  UTC time, causing schedules to fire at the wrong hour. Both the
  schedule executor in `main.py` and `get_next_occurrence()` in
  `schedule.py` now resolve the user's timezone from the
  `system_settings` table and work in local time.

- **Impossible temperature readings in chat/LLM context** -- the live
  HA fallback paths in `chat.py` (`get_zone_context` and
  `get_conditions_context`) had no validation, so a sensor reporting
  Fahrenheit without proper `unit_of_measurement` could pass through
  as raw Celsius (e.g., 68 degrees F stored as 68 degrees C). Added
  `_validate_temp_c()` helper that returns `None` for temps outside
  -40 to 60 degrees C. Applied to all DB read and HA fallback paths.
  Dashboard also validates zone temperatures before averaging.

- **Schedule time display** -- schedules page showed raw 24-hour
  format ("14:00") instead of 12-hour format ("2:00 PM"). Added
  `formatTime12h()` helper. Dashboard upcoming schedules also use
  `hour12: true` in `toLocaleTimeString`.

### Added

- **Active schedule indicator** -- new `GET /api/v1/schedules/active`
  endpoint that determines which schedule is currently running based
  on the user's timezone, day of week, and time window. Returns the
  highest-priority active schedule. The dashboard displays a green
  "Now Active" badge with a pulsing dot above the upcoming schedules
  list, showing the schedule name, target temperature, zones, and
  end time.

- **Compact thermostat set-temp card** -- new stat card in the
  dashboard stats grid (next to "Occupied") showing the current
  thermostat set-point with inline +/- buttons for quick adjustments.
  Uses a purple icon to differentiate from the orange average temp
  card. Clicking +/- immediately sends the override to the backend.

## 0.8.3

### Added

- **Ecobee schedule override** -- when ClimateIQ enters scheduled,
  active, or follow-me mode it now creates an Ecobee "vacation" hold
  (`ClimateIQ_Control`) to prevent the thermostat's internal schedule
  from reverting setpoints. Smart Home/Away and Follow Me are disabled
  so ClimateIQ has sole occupancy control. Switching back to learn mode
  deletes the hold and restores Ecobee's normal program.

- **Manual temperature override** -- new `POST /api/v1/system/override`
  endpoint for direct thermostat control with Ecobee hold management,
  plus `GET /api/v1/system/override` for current thermostat state.

- **Dashboard override UI** -- prominent manual override card on the
  dashboard with large +/- buttons, range slider, "Set Override" and
  "Resume Schedule" controls, and live thermostat status display.

- **ClimateIQ Lovelace card** -- HACS-compatible custom Lovelace card
  (`climateiq-card`) with dark glassmorphism theme. Displays current
  thermostat state, zone summary, quick actions, and manual override.
  Communicates with the add-on via HA ingress. Separate repo at
  `climateIQ-lovelace-card/`.

### Changed

- `ha_client.set_temperature_with_hold()` replaces plain
  `set_temperature()` in all mode executors (schedules, follow-me,
  active) to maintain Ecobee vacation holds automatically.

## 0.8.2

### Fixed

- **Chat history crash** -- fixed `ConversationHistoryItem` Pydantic
  validation error where the SQLAlchemy `metadata` descriptor (from
  `DeclarativeBase`) was returned instead of the JSONB column value.
  Applied the same fix to `ConversationResponse` and
  `UserFeedbackResponse` schemas.

- **Chat zone status accuracy** -- the LLM now falls back to live Home
  Assistant sensor states when DB readings are missing, preventing
  incorrect "offline" reports for zones that are actually online.
  Zone context explicitly labels zones as ONLINE with sensor counts.

### Added

- **Chat memory system** -- conversations are now mined for long-term
  user preferences and directives (e.g. "never heat the basement above
  65 F", "I prefer it cooler at night"). Extracted directives are stored
  in a new `user_directives` table and injected into both the chat
  system prompt and the Active-mode AI decision loop so the system
  remembers user preferences across sessions.

- **Directive management API** -- `GET /api/v1/chat/directives` to list
  active directives, `POST` to create manually, `DELETE` to deactivate.

- **Memory sidebar in Chat UI** -- the conversation sidebar now shows a
  "Memory" section listing all active directives with the ability to
  remove them.

## 0.8.1

### Changed

- **Dark glassmorphism UI redesign** — complete visual overhaul of the
  frontend with a dark glassmorphism aesthetic inspired by Humidity
  Intelligence V2. In dark mode, cards use translucent backgrounds with
  backdrop-blur, colored glow shadows, and gradient accents. Light mode
  uses clean solid backgrounds with subtle shadows.

- **Design system foundation** — new CSS custom properties for glass
  backgrounds, borders, and glow effects. Utility classes `.glass-card`
  and `.glow-border-*` for consistent glassmorphism across components.
  State-driven color tokens (safe, cool, warning, danger, purple).

- **Updated UI components** — Card, Button, Input, and ThemeToggle
  components updated with dark-mode translucent backgrounds, gradient
  buttons with glow, and glass input fields.

- **Layout shell redesign** — sidebar with dark glass background and
  glowing active nav items, header with lane-button mode switcher,
  ambient radial gradient overlays on the main container.

- **Dashboard redesign** — hero stat cards with glowing icon circles,
  instrument-panel typography (font-black), glass weather widget, glass
  schedule items, and glass temperature override popup.

- **ZoneCard redesign** — status-driven colored left border and glow
  shadow (green for occupied, sky for idle), pulse animation on occupied
  status dot, horizontal chip/pill layout for metrics.

- **Analytics redesign** — glass chart containers with updated tooltip
  styling, glass stat cards, glass tab navigation and time range
  selectors.

- **All pages updated** — Zones, Schedules, Chat, and Settings pages
  all updated with consistent glassmorphism treatment, refined
  typography (font-black for values, 10px bold uppercase labels), and
  dark-mode glass borders/backgrounds.

## 0.8.0

### Added

- **Zone metrics exclusion** — zones can now be excluded from analytics
  aggregates (overview, comfort scores, energy estimates) and from the AI
  control loop (RuleEngine comfort enforcement, PID vent optimization,
  PatternEngine learning). Useful for zones like basements that are
  intentionally kept at different temperatures and would skew whole-house
  metrics.

- **Month-based exclusion schedule** — exclusion can be limited to specific
  calendar months (e.g. Nov-Mar for a basement that's only excluded in
  winter). When no months are selected, the exclusion applies year-round.

- **Exclusion UI in zone settings** — new "Metrics & Control Exclusion"
  card on the zone detail page with a toggle, month selector buttons, and
  a status badge showing whether the zone is currently excluded or active.

- **`is_currently_excluded` computed field** on zone API responses — tells
  the frontend whether the zone is excluded right now based on the current
  month and the configured exclusion settings.

## 0.7.9

### Fixed

- **Single zone selection shows empty graph in Analytics** — selecting a
  single zone in the Temperature or Occupancy tabs showed "No data available"
  even when data existed. The single-zone path used a separate `/history`
  endpoint that picked a different (less-populated) TimescaleDB aggregate
  view than the multi-zone overview endpoint. Unified all zone selections
  (single, multi, all) to use the `/overview` endpoint consistently.

- **Humidity not showing in Analytics** — the Temperature tab's Humidity
  metric toggle showed an empty chart. The continuous aggregate views group
  by `(sensor_id, zone_id, bucket)`, so zones with separate temperature and
  humidity sensors produced multiple rows per time bucket. The backend
  returned these as separate readings, and the frontend overwrote real values
  with null from the other sensor's row. Fixed by re-aggregating across
  sensors in the overview SQL query (`GROUP BY zone_id, bucket`) and adding
  a frontend guard that never overwrites a non-null value with null.

## 0.7.8

### Added

- **Multi-zone selection in Analytics** — the zone selector on Temperature,
  Occupancy, and Energy tabs now supports selecting multiple specific zones
  (toggle buttons) in addition to "All Zones" or a single zone. Previously
  only "All Zones" or one zone at a time was possible.

- **`zone_ids` query parameter** on `/analytics/overview`, `/analytics/energy`,
  and `/analytics/comfort` endpoints — accepts a comma-separated list of zone
  UUIDs to filter results to a subset of zones.

- **Array parameter support in `buildUrl`** — the frontend API helper now
  supports `ParamValue[]` types, joining array values as comma-separated
  strings for query parameters.

## 0.7.7

### Added

- **ZoneManager wired into production** — the ZoneManager singleton is now
  initialized at startup, hydrated from the database with all active zones,
  and fed real-time sensor data from the WebSocket stream. Zone states are
  maintained with EMA-smoothed sensor values and live comfort scoring.

- **RuleEngine background task** — runs every 2 minutes to enforce comfort
  band limits, humidity control, occupancy-based setback adjustments, and
  anomaly detection across all zones.

- **PID Controller vent optimization** — per-zone PID controllers run every
  3 minutes, computing smart vent positions (10–100% open) based on the
  difference between current and target temperatures, with anti-windup and
  autotuning support.

- **PatternEngine learning** — occupancy and thermal pattern learning runs
  every 30 minutes, building per-zone models of typical occupancy schedules
  and thermal response characteristics for preconditioning.

- **Schedule preconditioning** — when a schedule is approaching, the pattern
  engine's thermal model is used to start heating/cooling early so the zone
  reaches the target temperature by the scheduled start time.

- **Schedule zone verification** — after a schedule fires, the system
  monitors zone sensors and sends a notification alert if any zone is more
  than 1.5°C off its target temperature after 15 minutes.

### Fixed

- **Cover automation crash** — `execute_cover_automation()` referenced
  `app_state.ha_client` which doesn't exist on the AppState dataclass.
  Fixed to use `_deps._ha_client`.

## 0.7.6

### Fixed

- **Hypertable primary key incompatibility** — TimescaleDB requires the
  partitioning column (`recorded_at`) to be part of all unique constraints.
  The `sensor_readings` and `device_actions` tables had UUID-only primary
  keys, causing `create_hypertable` to fail. Fixed by dropping the UUID-only
  PK and adding a composite primary key `(id, recorded_at)`.

- **Continuous aggregates fail inside transactions** — `CREATE MATERIALIZED
  VIEW ... WITH (timescaledb.continuous)` cannot run inside a transaction
  block. Previously these ran inside `engine.begin()` and silently failed.
  Now continuous aggregates are created on a separate AUTOCOMMIT connection.

## 0.7.5

### Fixed

- **Database init cascade failure** — `_ensure_timescaledb_objects()` ran all
  DDL inside a single transaction. When the first `create_hypertable` call
  failed, PostgreSQL put the transaction into `InFailedSqlTransaction` state,
  causing ALL subsequent DDL (continuous aggregates, policies) to silently
  fail. Restructured to use SAVEPOINTs so each DDL statement is isolated —
  a failure in one does not abort the rest.

### Changed

- **Dynamic version banner** — `run.sh` now reads the version from
  `/app/VERSION` at runtime instead of having it hardcoded. One fewer file
  to update on version bumps.

## 0.7.4

### Fixed

- **Sensor health check verifies with HA before alerting** — `check_sensor_health`
  previously relied solely on the `Sensor.last_seen` database timestamp to
  declare sensors offline. Now it pings the Home Assistant REST API
  (`get_state()`) to verify the entity is actually unavailable before sending
  a false offline notification.

- **Comfort scores raw fallback** — the `/analytics/comfort` endpoint now
  falls back to raw `sensor_readings` data when TimescaleDB continuous
  aggregate views don't exist, instead of returning empty results.

## 0.7.3

### Fixed

- **Analytics MissingGreenlet crash** — `get_overview()` called `db.rollback()`
  in except blocks, which expired Zone ORM objects. Subsequent access to
  `zone.id` / `zone.name` triggered synchronous lazy-loading inside an async
  context, raising `MissingGreenlet`. Fixed by eagerly capturing zone info
  (`[(z.id, z.name) for z in zones]`) before any rollback can occur.

- **Dashboard humidity display** — `_enrich_zone_response` queried the last
  50 `SensorReading` rows and picked the first match per field. Humidity
  readings were pushed out of the 50-row window by more frequent temperature
  updates. Replaced with 4 targeted queries (one per field: temperature,
  humidity, lux, occupancy), each fetching only the latest row.

- **Raw fallback for overview endpoint** — when TimescaleDB aggregate views
  are missing, the overview endpoint now falls back to querying raw
  `sensor_readings` instead of returning empty time series.

## 0.7.2

### Fixed

- **Sensor offline false alerts** — `check_sensor_health` was sending
  offline notifications based on stale `last_seen` timestamps even when
  sensors were actively reporting. Improved the health check logic to
  reduce false positives.

- **Analytics zero-data** — multiple analytics endpoints returned empty
  results due to query issues with the continuous aggregate views. Fixed
  query logic to properly handle missing or empty aggregate data.

- **Dashboard zone navigation** — clicking a zone card on the Dashboard
  now navigates to the zone detail view. Previously zone cards were not
  clickable.

## 0.7.1

### Added

- **Lux-driven cover automation** — new background task that monitors
  illuminance sensors and automatically adjusts cover/blind positions
  based on configurable lux thresholds. Closes covers when lux exceeds
  the high threshold (reduce solar heat gain) and opens them when below
  the low threshold.

- **Occupancy inference** — zones without dedicated occupancy sensors can
  now infer occupancy from motion sensor activity patterns and door
  sensor state changes.

### Improved

- **Dashboard enhancements** — zone cards show set temperature alongside
  current temperature, improved visual hierarchy and status indicators.

## 0.7.0

### Added

- **Lux display in zone detail view** — zones with illuminance sensors
  now show the current lux reading on a dedicated card in the zone
  detail page.

- **Occupancy display in zone detail view** — zones with occupancy or
  motion sensors now show the current occupancy state in the zone detail
  page.

### Fixed

- **Dashboard humidity not showing** — humidity values were missing from
  zone cards due to the sensor reading query window being too narrow.
  Initial fix applied here, with a more thorough fix in v0.7.3.

## 0.6.8

### Fixed

- **HA device registry uses WebSocket API** — replaced broken REST API calls
  (`/api/config/device_registry` and `/api/config/entity_registry` return 404)
  with HA WebSocket commands (`config/device_registry/list` and
  `config/entity_registry/list`). Added `send_command()` method to
  `HAWebSocketClient` for request/response WS commands.

- **TimescaleDB continuous aggregates created at startup** — `init_db()` now
  creates hypertables and the `sensor_readings_5min`, `sensor_readings_hourly`,
  and `sensor_readings_daily` continuous aggregate views if they don't exist,
  along with refresh and compression policies.

- **Analytics endpoints gracefully handle missing views** — `get_zone_history`,
  `get_overview`, and `get_comfort_scores` now catch `ProgrammingError` when
  aggregate views are missing and fall back to raw data or empty results
  instead of crashing with a 500 error.

## 0.6.7

### Added

- **HA device picker in Zones** — new "Add Device" button lets you select
  a Home Assistant device (e.g., a multi-sensor) and import all its
  sensor/binary_sensor entities at once with checkboxes. Uses the HA device
  and entity registry APIs to group entities by physical device.

- **`GET /settings/ha/devices` endpoint** — returns HA devices with their
  grouped sensor/binary_sensor entities (name, manufacturer, model, area).

- **`POST /sensors/bulk` endpoint** — creates multiple sensors at once from
  a device selection, with automatic deduplication and WS filter registration.

- **HA device/entity registry support** — `HAClient` now has
  `get_device_registry()` and `get_entity_registry()` methods for querying
  the HA REST API device/entity registries.

### Fixed

- **Schedule overlap check with legacy `zone_id`** — `check_schedule_overlap()`
  now falls back to the legacy `zone_id` field when `zone_ids` is empty,
  correctly detecting that different zones don't overlap.

- **`ScheduleCreate` now forbids extra fields** — changed from `extra="ignore"`
  to `extra="forbid"` so unknown fields are rejected with a 422 validation error.

## 0.6.6

### Fixed

- **Sensor data not showing in zones** — the HA WebSocket entity filter
  now includes all registered sensors' `ha_entity_id` values from the DB,
  not just entities listed in the config/settings. Previously, assigning a
  sensor to a zone would show the entity but state_changed events were
  silently dropped because the entity wasn't in the WebSocket filter.

- **Value extraction from HA entities** — `_parse_state_change()` now uses
  `device_class` and `unit_of_measurement` attributes (the standard HA way)
  to extract temperature, humidity, illuminance, and occupancy. Previously
  it only matched keywords in entity IDs (e.g., "temperature" had to appear
  in the entity ID string). Also converts Fahrenheit to Celsius automatically
  when `unit_of_measurement` is `°F`.

- **Entity filter reads from DB settings** — the WebSocket entity filter
  now reads `climate_entities` and `sensor_entities` from the
  `system_settings` KV table (set via the Settings UI), not just config.

### Added

- **Dynamic entity filter updates** — when a new sensor with an
  `ha_entity_id` is created, it is immediately added to the running
  WebSocket entity filter (no restart needed).

- **Searchable entity picker** — the sensor assignment form now has a
  search/filter input instead of a bare dropdown. Search by entity name,
  entity ID, or device class. Can also paste a custom entity ID directly.
  Shows entity state, device class, and unit of measurement in results.

- **Enhanced entity info** — the `GET /settings/ha/entities` endpoint now
  returns `domain`, `device_class`, and `unit_of_measurement` fields.

## 0.6.5

### Added

- **Whole-house analytics overview** — the Temperature and Occupancy tabs
  now default to an "All Zones" view showing every zone on a single chart.
  Temperature tab renders a multi-line chart (one colored line per zone)
  with a Temperature/Humidity metric toggle. Occupancy tab shows a grouped
  bar chart with per-zone occupancy rates by hour.

- **`GET /analytics/overview` backend endpoint** — new endpoint that queries
  TimescaleDB continuous aggregates for all active zones in a single query,
  returning per-zone time series with temperature, humidity, and occupancy.
  Automatically selects the optimal aggregate view (5min/hourly/daily) based
  on the lookback window.

### Improved

- **Extended color palette** — 16 distinct HSL colors for zone
  differentiation (up from 8) to support all 11 zones.

- **Zone selector** on Temperature and Occupancy tabs now includes an
  "All Zones" button (selected by default) alongside individual zone
  buttons for drill-down.

## 0.6.4

### Improved

- **Renamed "ClimateIQ Copilot" to "ClimateIQ Advisor"** in the chat UI.

- **AI chat now has full live system context** — the LLM system prompt
  now includes the current system mode, thermostat state (HVAC mode,
  current/target temp, preset, fan mode), all system settings, every
  enabled schedule with zone names and timing, and current weather data.
  The AI can now accurately answer "what mode is the system in?",
  "what's the thermostat set to?", "what schedules are active?", etc.

## 0.6.3

### Improved

- **Multi-zone schedule selection** — schedules now support selecting
  multiple specific zones (e.g., "all bedrooms") instead of only one zone
  or all zones. The zone picker uses toggle chips similar to the day-of-week
  selector, with "All zones" and "Select all" shortcuts.

- **Priority explanation** — the priority slider in the schedule form now
  includes helper text: 1-3 for defaults, 4-7 for regular schedules, 8-10
  for overrides. Higher priority schedules take precedence when overlapping.

### Changed

- **Schedule data model** — `zone_id` (single UUID) replaced by `zone_ids`
  (JSONB array of UUIDs). Empty array = all zones. The old `zone_id` column
  is preserved for backwards compatibility. A migration in `init_db()` auto-
  converts existing schedules on startup.

- **API schema** — `zone_id`/`zone_name` fields replaced by `zone_ids`/
  `zone_names` arrays on all schedule endpoints. Conflict detection updated
  to handle set-based zone overlap.

## 0.6.2

### Improved

- **Analytics now use TimescaleDB continuous aggregates** — the history
  and comfort endpoints no longer fetch all raw sensor readings into
  Python for aggregation. Instead, queries automatically select the best
  pre-computed view (5-min, hourly, or daily) based on the lookback
  window and requested resolution. For a 30-day query, this reduces the
  row count from ~86,400 per sensor to ~720 (hourly buckets). The comfort
  endpoint also replaced its N+1 per-zone query pattern with a single
  bulk SQL query grouped by zone_id.

### Fixed

- **LLM model list now populates** — the Settings > LLM Providers tab
  was always showing 0 models because the backend endpoints were stubs
  returning hardcoded empty lists. Now the listing and refresh endpoints
  call the existing `discover_models()` module which makes real API calls
  to each provider (Anthropic, OpenAI, Gemini, Grok, Ollama, LlamaCPP).
  Discovery runs in a background thread with a 5-second timeout to avoid
  blocking, and results are cached for 5 minutes. The response now
  includes model display names and context lengths.

### Added

- **Comprehensive diagnostics endpoint** `GET /system/diagnostics` —
  checks 11 system components in a single request: database connectivity,
  TimescaleDB extensions/hypertables/continuous aggregates, table row
  counts, Redis PING + SET/GET, Home Assistant REST + WebSocket,
  background scheduler job status, notification service, and LLM provider
  configuration. Returns structured results with per-component status,
  latency measurements, and an overall health assessment.

## 0.6.1

### Added

- **Logic Reference system** — new `GET /system/logic-reference` endpoint
  returns structured documentation of how ClimateIQ works (10 sections:
  architecture, modes, schedules, zones, thermostat, notifications, energy,
  weather, chat, data storage).

- **Settings > Logic tab** — new tab in Settings displays the full logic
  reference as styled cards. Users can read how every feature works
  without leaving the UI.

- **AI chat now has full system context** — the LLM system prompt now
  includes a condensed version of the logic reference, so the AI
  assistant can accurately explain how Follow-Me mode, schedules,
  Active/AI mode, and all other features work when asked.

## 0.6.0

### Added

- **Schedule management page** — new `/schedules` route with full CRUD
  UI. Create, edit, delete, enable/disable schedules. Day-of-week pills,
  time pickers, zone selector, target temp in user's unit, HVAC mode,
  priority slider. Conflict warnings displayed at top. Added to sidebar
  navigation with CalendarClock icon.

- **Schedule execution engine** — new background task (every 60s) that
  evaluates enabled schedules against the current time and fires them
  by calling `set_temperature` on the global thermostat. Uses a 2-minute
  match window with dedup to prevent re-firing. Converts C→F when HA
  is in Imperial. Records actions in `DeviceAction` table.

- **Follow-Me mode** — new background task (every 90s) that activates
  when system mode is `follow_me`. Reads occupancy from per-zone sensor
  readings, adjusts the global thermostat to the occupied zone's comfort
  preference temp. Multiple occupied zones get averaged. No occupancy
  falls back to eco temp (18°C/64°F). Only fires if target changes by
  more than 0.5°C.

- **Active/AI mode** — new background task (every 5m) that activates
  when system mode is `active`. Gathers all zone data, weather, current
  thermostat state, today's schedules, and comfort preferences. Asks
  the LLM to recommend an optimal temperature with reasoning. Applies
  safety clamps and only changes if diff > 0.5°C.

- **HA mobile app notifications** — `NotificationService` singleton
  initialized at startup, wired into schedule execution (confirms
  activations), sensor health checks (offline alerts), follow-me mode
  changes, and AI mode decisions. Uses `notification_target` setting
  from `system_settings` KV table (e.g., `mobile_app_joshua_s_iphone`).

### Removed

- Debug endpoint `GET /zones/debug/thermostat` (temporary, no longer
  needed).

## 0.5.4

### Added

- **Quick actions now work** — new `POST /system/quick-action` endpoint
  that controls the global thermostat directly via HA climate services.
  Actions: `eco` (set preset or lower by 3°), `away` (set Away preset),
  `boost_heat` (+2°), `boost_cool` (-2°), `resume` (clear preset).
  Previously quick actions went through the chat command parser which
  couldn't match the text patterns and had no access to the global
  thermostat (only per-zone devices which don't exist).

## 0.5.3

### Fixed

- **32°F / 0% no longer shown for zones without sensors** — the backend
  returned `0` (not `null`) for `current_temp` and `current_humidity`
  when no sensor data exists. Frontend now treats `0` as no-data and
  shows "--" instead of 32°F/0%.

## 0.5.2

### Fixed

- **Zones show "--" when no sensor is assigned** — temperature,
  humidity, status, and occupancy all show "--" instead of fake
  defaults (0%, Clear, 0°) when a zone has no sensor data. Only the
  target setpoint (shared from the global thermostat) shows a value.
  Per-zone values will appear once Zigbee sensors are assigned.

## 0.5.1

### Fixed

- **Zone current temp no longer shows thermostat reading** — the global
  climate fallback was setting `current_temp` on every zone from the
  Ecobee's own sensor, making all rooms show the same temperature.
  Now only the target setpoint is shared from the global thermostat.
  Current temp will show "--" until per-zone Zigbee sensors are assigned.

## 0.5.0

### Changed

- **Whole-house thermostat support** — zone enrichment no longer
  requires a thermostat device to be manually linked to each zone.
  When no per-zone thermostat device exists, the backend reads the
  global `climate_entities` setting (from DB or add-on config) and
  fetches live current temp + target setpoint from HA. Every zone
  gets the Ecobee's target temp; per-zone sensors (when installed)
  will override the current temp. The global climate state is cached
  for 15 seconds to avoid hitting HA once per zone.

## 0.4.9

### Added

- **Debug endpoint** `GET /api/v1/zones/debug/thermostat` — dumps raw
  HA thermostat entity state, attributes, DB capabilities, and HA unit
  system. Hit this to see exactly what HA is returning so we can fix
  the target temp mapping.

## 0.4.8

### Fixed

- **Ecobee target temp now correct** — Ecobee thermostats use
  `target_temp_low` (heat), `target_temp_high` (cool), or both (auto)
  instead of the generic `temperature` attribute. The backend now reads
  the HVAC mode and picks the correct setpoint. Previously `target_temp`
  came back null and the frontend showed the 22°C default (71.6°F).
- **Build fix** — missing closing braces in Dashboard onClick handlers,
  and `unitKey` declared after its use in `handleTempOverride`.

## 0.4.7

### Fixed

- **Thermostat temperatures now correct when HA uses Imperial (°F)** —
  the backend was storing raw HA temperature values without converting
  to Celsius. Since HA returns temps in the user's configured unit
  system, an HA instance set to Imperial would send 71°F which the
  backend stored as 71, and the frontend then converted "71°C" to
  159.8°F. Now the backend detects HA's unit system via `GET /api/config`
  (cached after first call) and converts F→C before storing. The
  frontend's C→display-unit conversion then produces correct values.

- **Dashboard temperature override respects user's unit** — the
  up/down temp override widget was hardcoded to a 10–35 range (Celsius)
  and sent raw values to the backend. Now uses the user's display unit
  (50–95°F or 10–35°C), seeds with the target temp converted to the
  display unit, and converts back to Celsius before sending.

## 0.4.6

### Fixed

- **Settings now persist to database** — `GET /settings` and
  `PUT /settings` were non-functional stubs that returned hardcoded
  defaults and silently discarded all writes. Rewrote the entire
  `settings.py` backend to properly read/write the `system_settings`
  KV table. All user preferences (timezone, temperature unit, comfort
  ranges, energy cost, currency, entity selections) now persist across
  add-on restarts.
- **Entity discovery endpoint** — `GET /settings/ha/entities?domain=...`
  now uses the initialized HA REST client to return real entities.
- **LLM provider listing** — `GET /settings/llm/providers` returns
  actual provider status based on configured environment variables.

## 0.4.5

### Fixed

- **Live thermostat data now actually works** — the HA REST client
  (`_ha_client`) was never initialized at startup. It was only created
  lazily when a route used `Depends(get_ha_client)`, but the zones
  endpoint reads the module-level variable directly. Since no route
  triggered the lazy init before zones were fetched, `_ha_client` was
  always `None` and the live HA thermostat block was silently skipped.
  Now the REST client is initialized during app startup in `lifespan()`,
  so `GET /zones` correctly returns the thermostat's live
  `current_temperature` and `temperature` (setpoint) attributes.

## 0.4.4

### Fixed

- **Temperature unit respect throughout UI** — all temperature displays
  now honor the user's chosen unit (°C or °F) from Settings. Previously
  every page hardcoded °C. Affected locations:
  - **Settings** — comfort temp min/max labels, input values, and
    live-converting when toggling the unit selector. Values convert back
    to Celsius before saving to the backend.
  - **Dashboard** — average temperature stat card, upcoming schedule
    target temps.
  - **Zones** — list view temperature stat, detail view avg/range stats,
    24-hour history chart data + legend, comfort preference labels/values
    and save-back conversion.
  - **Analytics** — temperature history chart data + legend, avg/min/max
    stat cards, comfort zone averages.
- Added `toDisplayTemp`, `toStorageCelsius`, and `tempUnitLabel` utility
  helpers in `lib/utils.ts`.

## 0.4.3

### Fixed

- **Sidebar fully opaque on mobile** — the sidebar used `bg-card/70`
  (70% opacity) with `backdrop-blur`, making text blurry and hard to read
  on phones where the main content bled through. Now uses solid `bg-card`
  on mobile and only applies the translucent glass effect on desktop
  (`lg:bg-card/70 lg:backdrop-blur`) where the sidebar is static.

## 0.4.2

### Fixed

- **Sidebar closes on navigation** — tapping a nav link on mobile now
  closes the sidebar automatically instead of leaving it covering the
  content. Added explicit `left-0` to the fixed sidebar for reliable
  positioning in HA ingress webviews. Overlay z-index lowered so sidebar
  links remain clickable.
- **Live thermostat data now shown** — zone enrichment now **prefers**
  live Home Assistant thermostat data (current_temperature, target
  temperature) over stale database readings. Previously, any existing DB
  sensor reading (even zeroes from init) would take priority and the live
  HA data was silently skipped.

## 0.4.1

### Fixed

- **Mobile responsiveness overhaul** — the entire UI is now usable on
  phones and the Home Assistant mobile app. No design changes; purely
  responsive adjustments:
  - Main sidebar defaults to closed on screens < 1024px instead of
    covering 77% of the viewport on load.
  - Chat conversation sidebar defaults to closed on mobile and uses a
    slide-over overlay (like the main sidebar) instead of stealing inline
    width.
  - Header mode-switcher buttons wrap and use smaller padding on narrow
    screens so all four modes remain accessible.
  - Analytics time-range selector stacks below the page title on mobile
    instead of overflowing off-screen.
  - Analytics tab labels hidden on mobile (icon-only), matching the
    Settings tab pattern.
  - ZoneCard stats grid switches from a fixed 3-column layout to
    single-column on mobile.
  - Zone detail and Analytics summary stats use `sm:grid-cols-2
    lg:grid-cols-4` instead of jumping from 1 to 4 columns at 640px.
  - Layout and Card padding reduced on mobile (`p-3`/`p-4` base,
    `sm:p-6` on larger screens).
  - Input font size set to 16px on mobile (`text-base sm:text-sm`) to
    prevent iOS Safari auto-zoom on focus.
  - Dashboard temperature override button is now visible on touch devices
    (was hidden behind hover-only opacity).
  - Temperature override controls enlarged from 24px to 32px for better
    touch targets.
  - Sidebar close and hamburger buttons enlarged for easier tapping.
  - Entity names in Settings truncated with hidden entity_id on mobile to
    prevent row overflow.
  - Entity filter search input uses full width on mobile.
  - `overflow-x: hidden` added to body to prevent horizontal scroll from
    any stray overflow.
  - Sensor form HA entity picker moved inside its parent grid so
    `sm:col-span-2` works correctly.
  - Chat "Send" label and "New Chat" label hidden on mobile (icon only).

## 0.4.0

### Added

- **Live thermostat data on Dashboard** — zone cards now show real-time
  current temperature and target temperature fetched directly from Home
  Assistant climate entities instead of relying on stale DB readings.
- **Energy entity integration** — new `energy_entity` add-on option lets
  users point to a real HA energy sensor (e.g., a utility meter). Energy
  card on the Dashboard reads live state from HA and only appears when an
  entity is configured — no more fabricated heuristic estimates.
- `GET /api/v1/analytics/energy/live` endpoint returning live energy
  reading from the configured HA entity.
- **HA entity picker for sensors** — the sensor creation form in the Zones
  page now shows a dropdown of available HA sensor entities. Selecting one
  auto-fills the sensor name and links the `ha_entity_id`.
- **Energy entity picker in Settings** — Settings > Home Assistant tab
  includes a new picker for selecting the energy monitoring entity.

### Changed

- `_enrich_zone_response` in the zones API now accepts an optional HA
  client and fetches live thermostat state (`current_temperature`,
  `temperature`) for devices that have an `ha_entity_id`.
- Dashboard energy card replaced: uses live HA data via
  `/analytics/energy/live` instead of the heuristic `/analytics/energy`
  endpoint.

### Removed

- Tuning/Settings button from the header (redundant with sidebar
  navigation).
- Heuristic energy trend indicators (`TrendingUp`/`TrendingDown`) from
  the Dashboard stats bar.

## 0.3.1

### Added

- **Entity discovery UI** — Settings > Home Assistant tab now shows
  interactive multi-select lists for climate and sensor entities, populated
  live from Home Assistant. Users can search, select, and save entity
  filters directly from the web UI instead of editing add-on YAML.
- `GET /api/v1/settings/ha/entities` endpoint with optional `domain`
  query parameter for discovering available HA entities.
- `climate_entities` and `sensor_entities` are now persisted in the
  database settings table so they survive add-on restarts when set via UI.

### Fixed

- **Layout gap in HA ingress** — removed a redundant spacer `div` in the
  Layout component that doubled the sidebar width, causing ~2 inches of
  blank space to the right of the navigation column.

## 0.3.0

### Added

- **Entity filtering** — choose which Home Assistant entities ClimateIQ
  monitors instead of subscribing to all state changes.
  - `climate_entities`: list of `climate.*` entity IDs to track.
  - `sensor_entities`: list of `sensor.*` / `binary_sensor.*` entity IDs to track.
  - `weather_entity`: single `weather.*` entity for forecast polling.
  - When lists are empty (the default), all entities in the supported domains
    are accepted (previous behavior).
- Seed `weather_entity` into the database `system_settings` table on startup
  when configured via add-on options, so the weather poller picks it up
  automatically.

## 0.2.11

### Fixed

- Make `CREATE EXTENSION` calls non-fatal during `init_db()` so startup
  succeeds when the DB user isn't a superuser (extensions must be
  pre-installed by an admin).

## 0.2.10

### Fixed

- URL-encode database username and password with `quote_plus` so special
  characters (like `@`) in passwords don't break the connection URL parsing.
  This was the root cause of the "Name does not resolve" errors.

## 0.2.9

### Changed

- Replace `asyncpg` with `psycopg` (psycopg3) as the async PostgreSQL driver.

## 0.2.7

### Fixed

- Pre-resolve DB hostname to an IP address before handing the URL to asyncpg so
  that `getaddrinfo` is never called inside the asyncio thread-pool (broken on
  Alpine musl). Resolution happens both in `run.sh` (shell-level) and in
  `database.py` (`_pre_resolve_url`) as a defense-in-depth measure.

## 0.2.6

### Fixed

- Force uvicorn to run on the built-in `asyncio` event loop across the add-on,
  Docker image, and local development setups to avoid uvloop DNS resolution
  failures on Alpine/musl.

### Changed

- Document asyncio loop requirement across README and DOCS so non-HA deployments
  mirror the Home Assistant runtime behavior.

## 0.2.0

### Removed

- MQTT support removed entirely (all sensor data comes via Home Assistant WebSocket)
- Embedded PostgreSQL and Redis removed from add-on (external services required)
- Nginx ingress proxy removed (uvicorn serves directly on ingress port)

### Added

- Configuration field descriptions in HA add-on UI (translations/en.yaml)

### Fixed

- Add-on config save error when ollama_url is empty (changed schema from url? to str?)

## 0.1.0 - Initial Release

### Added

- Home Assistant add-on with ingress support
- Lightweight container (FastAPI/Uvicorn serves API + frontend SPA directly)
- Requires external TimescaleDB and Redis
- Multi-zone HVAC management dashboard
- Real-time sensor monitoring via Home Assistant WebSocket
- AI-powered chat interface with multi-provider LLM support
  - Anthropic Claude
  - OpenAI GPT
  - Google Gemini
  - xAI Grok
  - Ollama (local inference)
- Smart scheduling with time-based temperature profiles
- Weather integration for proactive climate adjustments
- Energy usage analytics
- Support for amd64 and aarch64 architectures
