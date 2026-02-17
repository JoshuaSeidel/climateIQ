# Changelog

## 0.2.9

### Changed

- Replace `asyncpg` with `psycopg` (psycopg3) as the async PostgreSQL driver.
  `asyncpg` routes all connections through `asyncio.loop.create_connection()`
  which calls `getaddrinfo` in a thread-pool — broken on Alpine/musl.
  `psycopg` connects directly without this indirection.
- Remove all DNS resolver patches and workarounds from previous versions.

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
