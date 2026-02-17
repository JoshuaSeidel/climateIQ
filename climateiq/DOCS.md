# ClimateIQ Home Assistant Add-on

AI-Powered Intelligent Climate Management for Home Assistant.

## Overview

ClimateIQ is a smart HVAC zone management system that integrates directly with
Home Assistant. It provides real-time monitoring, AI-powered climate control
recommendations, and automated scheduling for multi-zone HVAC systems.

## Features

- **Multi-Zone Management** - Monitor and control individual HVAC zones
- **AI-Powered Recommendations** - Natural language climate control via LLM integration
- **Real-Time Monitoring** - Live temperature, humidity, and device status updates
- **Smart Scheduling** - Time-based and occupancy-aware temperature schedules
- **Weather Integration** - Proactive adjustments based on weather forecasts
- **Energy Analytics** - Track energy usage and identify savings opportunities
- **MQTT Support** - Auto-discovers MQTT broker from Home Assistant

## Installation

### From the Add-on Store

1. Navigate to **Settings > Add-ons > Add-on Store**
2. Click the three-dot menu and select **Repositories**
3. Add the repository URL: `https://github.com/joshuaseidel/climateiq`
4. Find "ClimateIQ" in the store and click **Install**
5. Configure the add-on options (see below)
6. Click **Start**

### Manual Installation

1. Copy the `ha-addon` directory to your Home Assistant `addons/climateiq` folder
2. Navigate to **Settings > Add-ons > Add-on Store**
3. Click the refresh button
4. Find "ClimateIQ" under **Local add-ons** and install it

## Configuration

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `log_level` | list | `info` | Logging verbosity: `debug`, `info`, `warning`, `error` |
| `mqtt_auto_discover` | bool | `true` | Auto-discover MQTT broker from Home Assistant |
| `temperature_unit` | list | `F` | Temperature display unit: `F` (Fahrenheit) or `C` (Celsius) |
| `use_internal_database` | bool | `true` | Use embedded PostgreSQL (false = external DB) |
| `use_internal_redis` | bool | `true` | Use embedded Redis (false = external Redis) |
| `external_db_host` | string | | External PostgreSQL/TimescaleDB host |
| `external_db_port` | port | `5432` | External DB port |
| `external_db_name` | string | `climateiq` | External DB name |
| `external_db_user` | string | `climateiq` | External DB user |
| `external_db_password` | string | | External DB password |
| `external_db_ssl` | bool | `false` | Enable SSL for external DB |
| `external_redis_url` | url | | External Redis URL (e.g., `redis://host:6379/0`) |
| `anthropic_api_key` | string | | API key for Anthropic Claude (AI chat features) |
| `openai_api_key` | string | | API key for OpenAI (AI chat features) |
| `gemini_api_key` | string | | API key for Google Gemini (AI chat features) |
| `grok_api_key` | string | | API key for xAI Grok (AI chat features) |
| `ollama_url` | url | | URL for local Ollama instance (e.g., `http://192.168.1.100:11434`) |

### Example Configuration

```yaml
log_level: info
mqtt_auto_discover: true
temperature_unit: F
use_internal_database: true
use_internal_redis: true
external_db_host: ""
external_db_port: 5432
external_db_name: climateiq
external_db_user: climateiq
external_db_password: ""
external_db_ssl: false
external_redis_url: ""
anthropic_api_key: sk-ant-...
openai_api_key: ""
gemini_api_key: ""
grok_api_key: ""
ollama_url: ""
```

## Accessing the UI

Once started, ClimateIQ appears as a panel in the Home Assistant sidebar
(look for the thermostat icon). Click it to open the ClimateIQ dashboard.

You can also access it directly at:
`http://your-ha-instance:8420` (if the port is exposed in the add-on configuration).

## Architecture

The add-on runs as a single container with embedded services:

- **Nginx** - Ingress proxy handling HA authentication and path rewriting
- **PostgreSQL** - Persistent storage for zones, sensors, schedules, and analytics
- **Redis** - Caching and real-time pub/sub for WebSocket updates
- **ClimateIQ Backend** - FastAPI application serving the API and frontend

All data is persisted in the `/data` directory, which survives add-on updates.

## MQTT Integration

When `mqtt_auto_discover` is enabled, ClimateIQ automatically discovers your
MQTT broker configuration from Home Assistant's Supervisor API. This means you
don't need to manually configure MQTT connection details.

ClimateIQ subscribes to climate-related MQTT topics and can publish commands
to control HVAC equipment directly.

## AI Chat Features

ClimateIQ includes an AI-powered chat interface for natural language climate
control. To use this feature, configure at least one LLM provider API key in
the add-on options.

Supported providers:
- **Anthropic Claude** - Recommended for best results
- **OpenAI GPT** - GPT-4 and GPT-3.5 support
- **Google Gemini** - Gemini Pro support
- **xAI Grok** - Grok support
- **Ollama** - Local LLM inference (no API key required)

## Troubleshooting

### Add-on won't start

1. Check the add-on logs in **Settings > Add-ons > ClimateIQ > Log**
2. Ensure no other service is using port 8420
3. Verify you have sufficient system resources (minimum 512MB RAM recommended)

### Can't access the UI through ingress

1. Try clearing your browser cache
2. Restart the add-on
3. Check that `ingress: true` is set in the add-on configuration

### MQTT not connecting

1. Verify MQTT is configured in Home Assistant (**Settings > Devices & Services > MQTT**)
2. Check that `mqtt_auto_discover` is enabled
3. Review the add-on logs for MQTT connection errors

### Database errors

The embedded PostgreSQL database is automatically initialized on first start.
If you encounter database errors after an update:

1. Stop the add-on
2. Check logs for migration errors
3. If needed, the database can be reset by removing `/data/postgresql` from the
   add-on data directory (this will erase all ClimateIQ data)

## Data Persistence

All ClimateIQ data is stored in the `/data` directory:

- `/data/postgresql/` - PostgreSQL database files
- `/data/redis/` - Redis persistence files
- `/data/climateiq/` - Application configuration and secrets

This data persists across add-on restarts and updates. Uninstalling the add-on
will remove this data.

## Support

- **GitHub Issues**: [github.com/joshuaseidel/climateiq/issues](https://github.com/joshuaseidel/climateiq/issues)
- **Documentation**: [github.com/joshuaseidel/climateiq](https://github.com/joshuaseidel/climateiq)
