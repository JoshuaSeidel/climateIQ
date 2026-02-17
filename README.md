# ClimateIQ

Intelligent HVAC zone management system with AI-powered automation, real-time monitoring, and natural language control.

## Features

- **Multi-Zone Control**: Manage multiple HVAC zones with individual temperature targets
- **AI Assistant**: Natural language interface for climate control (Claude, GPT, Gemini, Grok, Ollama)
- **Weather Integration**: Proactive pre-conditioning based on weather forecasts
- **Smart Scheduling**: Time-based schedules with conflict resolution
- **Real-time Monitoring**: WebSocket-based live updates for all sensors
- **Home Assistant Integration**: Full add-on with ingress, sidebar panel, and entity discovery
- **Energy Optimization**: Cost-aware decisions based on energy pricing

## Tech Stack

### Backend
- **Python 3.13+** with FastAPI
- **PostgreSQL 18** / **TimescaleDB** for time-series data
- **psycopg 3** (async PostgreSQL driver)
- **Redis 8+** for caching and pub/sub
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
- PostgreSQL / TimescaleDB instance
- Redis instance
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
   - Redis URL
   - Home Assistant URL and token
   - LLM API keys

4. Start with Docker Compose:
```bash
docker-compose up -d
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
uvicorn backend.api.main:app --reload --loop asyncio
```

#### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLIMATEIQ_DB_HOST` | Yes | `localhost` | PostgreSQL host |
| `CLIMATEIQ_DB_PORT` | No | `5432` | PostgreSQL port |
| `CLIMATEIQ_DB_NAME` | No | `climateiq` | Database name |
| `CLIMATEIQ_DB_USER` | No | `climateiq` | Database user |
| `CLIMATEIQ_DB_PASSWORD` | Yes | | Database password |
| `CLIMATEIQ_REDIS_URL` | Yes | `redis://localhost:6379/0` | Redis connection URL |
| `CLIMATEIQ_HOME_ASSISTANT_URL` | No | `http://localhost:8123` | Home Assistant URL |
| `CLIMATEIQ_HOME_ASSISTANT_TOKEN` | No | | HA long-lived access token |

### Database Setup

ClimateIQ requires PostgreSQL with these extensions pre-installed by a superuser:

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";    -- pgvector for embeddings/RAG
```

If using **TimescaleDB**, also enable the TimescaleDB extension. The app user
does not need superuser privileges — just ensure the extensions exist before
first startup.

### LLM Providers

Configure at least one:

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
2. Set `CLIMATEIQ_HOME_ASSISTANT_URL` and `CLIMATEIQ_HOME_ASSISTANT_TOKEN`
3. ClimateIQ will discover climate and sensor entities via the HA WebSocket API

## Home Assistant Add-on

ClimateIQ includes a full Home Assistant add-on with ingress proxying. The UI and all API calls are routed through Home Assistant when used as an add-on.

### Add-on Installation

1. Go to **Settings > Add-ons > Add-on Store**
2. Add repository: `https://github.com/JoshuaSeidel/climateIQ`
3. Install **ClimateIQ** and configure the options
4. Start the add-on — it appears in the HA sidebar

### Add-on Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `db_host` | string | **Yes** | | PostgreSQL/TimescaleDB host (IP or hostname) |
| `db_port` | port | No | `5432` | Database port |
| `db_name` | string | No | `climateiq` | Database name |
| `db_user` | string | No | `climateiq` | Database user |
| `db_password` | password | **Yes** | | Database password |
| `redis_url` | string | **Yes** | | Redis URL (e.g., `redis://192.168.1.50:6379/0`) |
| `log_level` | list | No | `info` | `debug`, `info`, `warning`, `error` |
| `temperature_unit` | list | No | `F` | `F` or `C` |
| `anthropic_api_key` | string | No | | Anthropic API key |
| `openai_api_key` | string | No | | OpenAI API key |
| `gemini_api_key` | string | No | | Google Gemini API key |
| `grok_api_key` | string | No | | xAI Grok API key |
| `ollama_url` | string | No | | Ollama instance URL |

### Database Requirements

The add-on requires **external** PostgreSQL and Redis. Before first startup,
connect to your database as a superuser and run:

```sql
CREATE DATABASE climateiqdb;
CREATE USER climateiq WITH PASSWORD 'your-password';
GRANT ALL PRIVILEGES ON DATABASE climateiqdb TO climateiq;

\c climateiqdb
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
```

**TimescaleDB** is recommended for time-series performance but not required.

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
│  │  React    │  │  FastAPI  │  │PostgreSQL │  │  Redis   │ │
│  │  Frontend │◄─┤  Backend  ├──┤/Timescale │  │  Cache   │ │
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
│  │ │Anthr│ │OpenAI│ │Gemini│ │Grok │ │ HA  ││               │
│  │ │opic │ │     │ │     │ │    │ │ WS  ││               │
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
