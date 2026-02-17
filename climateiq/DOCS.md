# ClimateIQ Home Assistant Add-on

AI-Powered Intelligent Climate Management for Home Assistant.

## Overview

ClimateIQ is a smart HVAC zone management system that integrates directly with
Home Assistant. It provides real-time monitoring, AI-powered climate control
recommendations, and automated scheduling for multi-zone HVAC systems.

## Features

- **Multi-Zone Management** - Monitor and control individual HVAC zones
- **AI-Powered Recommendations** - Natural language climate control via LLM integration
- **Real-Time Monitoring** - Live temperature, humidity, and device status updates via WebSocket
- **Smart Scheduling** - Time-based and occupancy-aware temperature schedules
- **Weather Integration** - Proactive adjustments based on weather forecasts
- **Energy Analytics** - Track energy usage and identify savings opportunities

## Prerequisites

ClimateIQ requires **external** database and cache services:

- **TimescaleDB (PostgreSQL 18)** or compatible PostgreSQL instance
- **Redis** for caching and real-time pub/sub

These can be other Home Assistant add-ons (e.g., the TimescaleDB and Redis
add-ons) or external services on your network.

## Installation

### From the Add-on Store

1. Navigate to **Settings > Add-ons > Add-on Store**
2. Click the three-dot menu and select **Repositories**
3. Add the repository URL: `https://github.com/JoshuaSeidel/climateIQ`
4. Find "ClimateIQ" in the store and click **Install**
5. Configure the add-on options (see below) — database and Redis are required
6. Click **Start**

## Configuration

### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `db_host` | string | **Yes** | | PostgreSQL/TimescaleDB host |
| `db_port` | port | No | `5432` | Database port |
| `db_name` | string | No | `climateiq` | Database name |
| `db_user` | string | No | `climateiq` | Database user |
| `db_password` | password | **Yes** | | Database password |
| `redis_url` | string | **Yes** | | Redis URL (e.g., `redis://host:6379/0`) |
| `log_level` | list | No | `info` | Logging verbosity: `debug`, `info`, `warning`, `error` |
| `temperature_unit` | list | No | `F` | Temperature display unit: `F` or `C` |
| `anthropic_api_key` | string | No | | API key for Anthropic Claude (AI chat) |
| `openai_api_key` | string | No | | API key for OpenAI (AI chat) |
| `gemini_api_key` | string | No | | API key for Google Gemini (AI chat) |
| `grok_api_key` | string | No | | API key for xAI Grok (AI chat) |
| `ollama_url` | string | No | | URL for local Ollama instance |

### Example Configuration

```yaml
log_level: info
temperature_unit: F
db_host: "192.168.1.50"
db_port: 5432
db_name: climateiq
db_user: climateiq
db_password: "your-secure-password"
redis_url: "redis://192.168.1.50:6379/0"
anthropic_api_key: "sk-ant-..."
```

### Using HA Add-ons for Database and Redis

If you run TimescaleDB and Redis as HA add-ons on the same machine:

```yaml
db_host: "core-timescaledb"       # or the add-on's hostname
db_port: 5432
db_name: climateiq
db_user: climateiq
db_password: "your-password"
redis_url: "redis://core-redis:6379/0"
```

## Accessing the UI

Once started, ClimateIQ appears as a panel in the Home Assistant sidebar
(look for the thermostat icon). Click it to open the ClimateIQ dashboard.

## Architecture

The add-on runs a single lightweight container:

- **ClimateIQ Backend (FastAPI/Uvicorn)** - Serves the API and pre-built
  frontend SPA directly on the HA ingress port

All persistent data (zones, schedules, analytics) is stored in the external
database. The add-on itself stores only a secret key in `/data/climateiq/`.

## AI Chat Features

ClimateIQ includes an AI-powered chat interface for natural language climate
control. Configure at least one LLM provider API key to use this feature.

Supported providers:
- **Anthropic Claude** - Recommended for best results
- **OpenAI GPT** - GPT-4 and GPT-3.5 support
- **Google Gemini** - Gemini Pro support
- **xAI Grok** - Grok support
- **Ollama** - Local LLM inference (no API key required)

## Troubleshooting

### Add-on won't start

1. Check the add-on logs in **Settings > Add-ons > ClimateIQ > Log**
2. Verify `db_host`, `db_password`, and `redis_url` are configured
3. Ensure the database and Redis services are running and reachable

### Can't access the UI through ingress

1. Try clearing your browser cache
2. Restart the add-on
3. Check that the add-on shows as "running" in the add-on info page

### Database connection errors

1. Verify the database host is reachable from the HA machine
2. Check that the database user and password are correct
3. Ensure the database exists and the user has access
4. The `uuid-ossp` and `vector` (pgvector) extensions must be created by a
   superuser before first startup:
   ```sql
   \c climateiqdb
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   CREATE EXTENSION IF NOT EXISTS "vector";
   ```
5. If the password contains special characters (`@`, `:`, `/`, etc.) ensure
   you're on v0.2.10+ which URL-encodes credentials automatically

## Support

- **GitHub Issues**: [github.com/JoshuaSeidel/climateIQ/issues](https://github.com/JoshuaSeidel/climateIQ/issues)
- **Documentation**: [github.com/JoshuaSeidel/climateIQ](https://github.com/JoshuaSeidel/climateIQ)
