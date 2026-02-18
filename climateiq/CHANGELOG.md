# Changelog

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
