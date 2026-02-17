# Changelog

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
