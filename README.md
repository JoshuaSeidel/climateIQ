# ClimateIQ

Intelligent HVAC zone management system with AI-powered automation, real-time monitoring, and natural language control.

## Features

- **Multi-Zone Control**: Manage multiple HVAC zones with individual temperature targets
- **AI Assistant**: Natural language interface for climate control (Claude, GPT, Gemini, Grok, Ollama)
- **Weather Integration**: Proactive pre-conditioning based on weather forecasts
- **Smart Scheduling**: Time-based schedules with conflict resolution
- **Real-time Monitoring**: WebSocket-based live updates for all sensors
- **Home Assistant Integration**: Seamless connection with your existing HA setup
- **Energy Optimization**: Cost-aware decisions based on energy pricing

## Tech Stack

### Backend
- **Python 3.13+** with FastAPI
- **TimescaleDB** (PostgreSQL 18 with time-series optimization)
- **Redis 8+** for caching
- **MQTT** for real-time sensor data
- **LiteLLM** for unified LLM provider access

### Frontend
- **React 19.2+** with TypeScript 5.7+
- **Vite 6+** build system
- **TailwindCSS 4+** for styling
- **TanStack Query** for server state
- **Zustand** for client state

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 24 LTS
- Python 3.13+
- At least one LLM API key (Anthropic, OpenAI, Gemini, etc.)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/JoshuaSeidel/climateiq.git
cd climateiq
```

2. Copy environment configuration:
```bash
cp .env.example .env
```

3. Edit `.env` with your configuration:
   - Database credentials
   - MQTT broker settings
   - Home Assistant URL and token
   - LLM API keys

4. Start with Docker Compose (pulls prebuilt image from GHCR):
```bash
docker-compose up -d
```

### Local Build (from source)

Use the local compose file when you want to build from source:

```bash
docker-compose -f docker-compose.local.yml up --build
```

5. Access the application:
   - Web UI: http://localhost:8420
   - API Docs: http://localhost:8420/docs

### Development Setup

#### Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

#### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Configuration

### LLM Providers

ClimateIQ supports multiple LLM providers. Configure at least one:

| Provider | Environment Variable | Models |
|----------|---------------------|--------|
| Anthropic | `ANTHROPIC_API_KEY` | Claude 4 Sonnet, Opus, Haiku |
| OpenAI | `OPENAI_API_KEY` | GPT-4o, GPT-4o-mini |
| Google | `GEMINI_API_KEY` | Gemini 2.0 Flash, Pro |
| xAI | `GROK_API_KEY` | Grok |
| Ollama | `OLLAMA_URL` | Any local model |

Models are discovered dynamically from each provider's API.

### Home Assistant

1. Generate a long-lived access token in HA
2. Set `HA_URL` and `HA_TOKEN` in `.env`
3. ClimateIQ will discover climate and sensor entities

### MQTT

Configure your MQTT broker (typically from Home Assistant):

```env
MQTT_BROKER=192.168.1.x
MQTT_PORT=1883
MQTT_USERNAME=your_user
MQTT_PASSWORD=your_password
```

## Home Assistant Add-on

ClimateIQ includes a full Home Assistant add-on with ingress proxying. The UI and all API calls are routed through Home Assistant when used as an add-on.

### Add-on Options

You can choose to use **internal** PostgreSQL/Redis (embedded in the add-on container) or **external** services:

```yaml
use_internal_database: true
use_internal_redis: true
external_db_host: ""
external_db_port: 5432
external_db_name: climateiq
external_db_user: climateiq
external_db_password: ""
external_db_ssl: false
external_redis_url: ""
```

### Database Requirements

- **pgvector** is **required** for embeddings and RAG.
- **TimescaleDB** is **preferred** for time-series performance (hypertables, continuous aggregates, compression).
- Internal add-on DB uses standard PostgreSQL. For full time-series performance, use an **external TimescaleDB** instance.

## API Endpoints

### Zones
- `GET /api/v1/zones` - List all zones
- `POST /api/v1/zones` - Create zone
- `GET /api/v1/zones/{id}` - Get zone details
- `PUT /api/v1/zones/{id}` - Update zone
- `DELETE /api/v1/zones/{id}` - Delete zone

### Chat
- `POST /api/v1/chat` - Send message to AI
- `GET /api/v1/chat/history` - Get conversation history
- `WebSocket /ws/chat` - Real-time chat

### Weather
- `GET /api/v1/weather/current` - Current conditions
- `GET /api/v1/weather/forecast` - Multi-day forecast
- `GET /api/v1/weather/hourly` - Hourly forecast

### Schedules
- `GET /api/v1/schedules` - List schedules
- `POST /api/v1/schedules` - Create schedule
- `GET /api/v1/schedules/upcoming` - Next 24h events

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     ClimateIQ System                        │
├─────────────────────────────────────────────────────────────┤
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌──────────┐ │
│  │  React    │  │  FastAPI  │  │TimescaleDB│  │  Redis   │ │
│  │  Frontend │◄─┤  Backend  ├──┤  Database │  │  Cache   │ │
│  └───────────┘  └─────┬─────┘  └───────────┘  └──────────┘ │
│                       │                                     │
│  ┌────────────────────┼────────────────────┐               │
│  │                    ▼                    │               │
│  │    ┌───────────────────────────┐       │               │
│  │    │    Decision Engine        │       │               │
│  │    │  (AI + Rules + Learning)  │       │               │
│  │    └───────────────────────────┘       │               │
│  │                    │                    │               │
│  │    ┌───────┬───────┼───────┬───────┐   │               │
│  │    ▼       ▼       ▼       ▼       ▼   │               │
│  │ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐│               │
│  │ │Anthr│ │OpenAI│ │Gemini│ │HA  │ │MQTT ││               │
│  │ │opic │ │     │ │     │ │    │ │     ││               │
│  │ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘│               │
│  └────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

## License

MIT License - See [LICENSE](LICENSE) for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

- GitHub Issues: [Report a bug](https://github.com/JoshuaSeidel/climateiq/issues)
- Documentation: Coming soon
