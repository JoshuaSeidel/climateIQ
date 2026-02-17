# Changelog

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
