#!/usr/bin/env bash
# ClimateIQ Home Assistant Add-on Startup Script
# Manages PostgreSQL, Redis, Nginx, and the ClimateIQ backend
set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

CONFIG_PATH="/data/options.json"
DATA_DIR="/data/climateiq"
PG_DATA="/data/postgresql/data"
LOG_DIR="/var/log/climateiq"

# Read options from HA add-on config
if [ -f "$CONFIG_PATH" ]; then
    LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG_PATH")
    MQTT_AUTO_DISCOVER=$(jq -r '.mqtt_auto_discover // true' "$CONFIG_PATH")
    TEMPERATURE_UNIT=$(jq -r '.temperature_unit // "F"' "$CONFIG_PATH")
    USE_INTERNAL_DATABASE=$(jq -r '.use_internal_database // true' "$CONFIG_PATH")
    USE_INTERNAL_REDIS=$(jq -r '.use_internal_redis // true' "$CONFIG_PATH")
    EXTERNAL_DB_HOST=$(jq -r '.external_db_host // ""' "$CONFIG_PATH")
    EXTERNAL_DB_PORT=$(jq -r '.external_db_port // 5432' "$CONFIG_PATH")
    EXTERNAL_DB_NAME=$(jq -r '.external_db_name // "climateiq"' "$CONFIG_PATH")
    EXTERNAL_DB_USER=$(jq -r '.external_db_user // "climateiq"' "$CONFIG_PATH")
    EXTERNAL_DB_PASSWORD=$(jq -r '.external_db_password // ""' "$CONFIG_PATH")
    EXTERNAL_DB_SSL=$(jq -r '.external_db_ssl // false' "$CONFIG_PATH")
    EXTERNAL_REDIS_URL=$(jq -r '.external_redis_url // ""' "$CONFIG_PATH")
    ANTHROPIC_API_KEY=$(jq -r '.anthropic_api_key // ""' "$CONFIG_PATH")
    OPENAI_API_KEY=$(jq -r '.openai_api_key // ""' "$CONFIG_PATH")
    GEMINI_API_KEY=$(jq -r '.gemini_api_key // ""' "$CONFIG_PATH")
    GROK_API_KEY=$(jq -r '.grok_api_key // ""' "$CONFIG_PATH")
    OLLAMA_URL=$(jq -r '.ollama_url // ""' "$CONFIG_PATH")
else
    LOG_LEVEL="info"
    MQTT_AUTO_DISCOVER="true"
    TEMPERATURE_UNIT="F"
    USE_INTERNAL_DATABASE="true"
    USE_INTERNAL_REDIS="true"
    EXTERNAL_DB_HOST=""
    EXTERNAL_DB_PORT="5432"
    EXTERNAL_DB_NAME="climateiq"
    EXTERNAL_DB_USER="climateiq"
    EXTERNAL_DB_PASSWORD=""
    EXTERNAL_DB_SSL="false"
    EXTERNAL_REDIS_URL=""
    ANTHROPIC_API_KEY=""
    OPENAI_API_KEY=""
    GEMINI_API_KEY=""
    GROK_API_KEY=""
    OLLAMA_URL=""
fi

echo "============================================"
echo "  ClimateIQ Home Assistant Add-on v0.1.0"
echo "============================================"
echo "Log level:          ${LOG_LEVEL}"
echo "MQTT auto-discover: ${MQTT_AUTO_DISCOVER}"
echo "Temperature unit:   ${TEMPERATURE_UNIT}"
echo "Internal DB:        ${USE_INTERNAL_DATABASE}"
echo "Internal Redis:     ${USE_INTERNAL_REDIS}"
echo "============================================"

# =============================================================================
# Validate external service configuration
# =============================================================================

if [ "$USE_INTERNAL_DATABASE" != "true" ]; then
    if [ -z "$EXTERNAL_DB_HOST" ] || [ -z "$EXTERNAL_DB_NAME" ] || [ -z "$EXTERNAL_DB_USER" ] || [ -z "$EXTERNAL_DB_PASSWORD" ]; then
        echo "[ClimateIQ] ERROR: External database is enabled but required fields are missing."
        echo "[ClimateIQ] Set external_db_host, external_db_name, external_db_user, external_db_password."
        exit 1
    fi
fi

if [ "$USE_INTERNAL_REDIS" != "true" ]; then
    if [ -z "$EXTERNAL_REDIS_URL" ]; then
        echo "[ClimateIQ] ERROR: External Redis is enabled but external_redis_url is missing."
        exit 1
    fi
fi

# =============================================================================
# Signal handling for graceful shutdown
# =============================================================================

PIDS=()

cleanup() {
    echo "[ClimateIQ] Shutting down gracefully..."

    # Kill child processes in reverse order
    for ((i=${#PIDS[@]}-1; i>=0; i--)); do
        pid="${PIDS[$i]}"
        if kill -0 "$pid" 2>/dev/null; then
            echo "[ClimateIQ] Stopping PID $pid..."
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait for processes to exit
    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    # Stop PostgreSQL cleanly
    if [ -f /run/postgresql/postmaster.pid ]; then
        echo "[ClimateIQ] Stopping PostgreSQL..."
        su-exec postgres pg_ctl stop -D "$PG_DATA" -m fast 2>/dev/null || true
    fi

    echo "[ClimateIQ] Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP

# =============================================================================
# Start PostgreSQL (optional)
# =============================================================================

if [ "$USE_INTERNAL_DATABASE" = "true" ]; then
    echo "[ClimateIQ] Starting PostgreSQL..."

    # Initialize database if needed
    if [ ! -f "$PG_DATA/PG_VERSION" ]; then
        echo "[ClimateIQ] Initializing PostgreSQL database..."
        mkdir -p "$PG_DATA"
        chown -R postgres:postgres /data/postgresql /run/postgresql
        su-exec postgres initdb -D "$PG_DATA" --auth=trust --encoding=UTF8

        # Configure for embedded use
        cat >> "$PG_DATA/postgresql.conf" <<PGCONF
listen_addresses = '127.0.0.1'
port = 5432
max_connections = 20
shared_buffers = 64MB
work_mem = 4MB
logging_collector = off
log_destination = 'stderr'
PGCONF
    fi

    chown -R postgres:postgres /data/postgresql /run/postgresql /var/log/postgresql
    su-exec postgres pg_ctl start -D "$PG_DATA" -l /var/log/postgresql/postgresql.log -w -t 30

    # Create database and user if they don't exist
    echo "[ClimateIQ] Ensuring database exists..."
    su-exec postgres psql -c "SELECT 1 FROM pg_roles WHERE rolname='climateiq'" | grep -q 1 || \
        su-exec postgres psql -c "CREATE ROLE climateiq WITH LOGIN PASSWORD 'climateiq';"
    su-exec postgres psql -lqt | cut -d \| -f 1 | grep -qw climateiq || \
        su-exec postgres psql -c "CREATE DATABASE climateiq OWNER climateiq;"

    # Enable extensions (required)
    su-exec postgres psql -d climateiq -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";" 2>/dev/null || true
    su-exec postgres psql -d climateiq -c "CREATE EXTENSION IF NOT EXISTS \"pgvector\";" 2>/dev/null || true

    echo "[ClimateIQ] PostgreSQL ready."
else
    echo "[ClimateIQ] Internal PostgreSQL disabled. Using external DB."
fi

# =============================================================================
# Start Redis (optional)
# =============================================================================

if [ "$USE_INTERNAL_REDIS" = "true" ]; then
    echo "[ClimateIQ] Starting Redis..."
    mkdir -p /data/redis
    redis-server /etc/redis-climateiq.conf &
    PIDS+=($!)

    # Wait for Redis to be ready
    for i in $(seq 1 30); do
        if redis-cli ping 2>/dev/null | grep -q PONG; then
            break
        fi
        sleep 0.5
    done
    echo "[ClimateIQ] Redis ready."
else
    echo "[ClimateIQ] Internal Redis disabled. Using external Redis."
fi

# =============================================================================
# MQTT Auto-Discovery via HA Supervisor
# =============================================================================

MQTT_BROKER=""
MQTT_PORT=""
MQTT_USERNAME=""
MQTT_PASSWORD=""

if [ "$MQTT_AUTO_DISCOVER" = "true" ] && [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    echo "[ClimateIQ] Discovering MQTT configuration from Home Assistant..."
    MQTT_RESPONSE=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        http://supervisor/services/mqtt 2>/dev/null || echo "{}")

    if echo "$MQTT_RESPONSE" | jq -e '.data.host' >/dev/null 2>&1; then
        MQTT_BROKER=$(echo "$MQTT_RESPONSE" | jq -r '.data.host')
        MQTT_PORT=$(echo "$MQTT_RESPONSE" | jq -r '.data.port')
        MQTT_USERNAME=$(echo "$MQTT_RESPONSE" | jq -r '.data.username // ""')
        MQTT_PASSWORD=$(echo "$MQTT_RESPONSE" | jq -r '.data.password // ""')
        echo "[ClimateIQ] MQTT discovered: ${MQTT_BROKER}:${MQTT_PORT}"
    else
        echo "[ClimateIQ] MQTT service not found in Home Assistant."
    fi
fi

# =============================================================================
# Configure Nginx for Ingress
# =============================================================================

echo "[ClimateIQ] Configuring Nginx ingress proxy..."

# Get the ingress entry from Supervisor
INGRESS_ENTRY=""
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    ADDON_INFO=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        http://supervisor/addons/self/info 2>/dev/null || echo "{}")
    INGRESS_ENTRY=$(echo "$ADDON_INFO" | jq -r '.data.ingress_entry // ""')
fi

echo "[ClimateIQ] Ingress entry: ${INGRESS_ENTRY:-not available}"

# Start Nginx
nginx -t 2>/dev/null && nginx &
PIDS+=($!)
echo "[ClimateIQ] Nginx ready."

# =============================================================================
# Run Database Migrations
# =============================================================================

echo "[ClimateIQ] Running database migrations..."

if [ "$USE_INTERNAL_DATABASE" = "true" ]; then
    export CLIMATEIQ_DB_HOST="127.0.0.1"
    export CLIMATEIQ_DB_PORT="5432"
    export CLIMATEIQ_DB_NAME="climateiq"
    export CLIMATEIQ_DB_USER="climateiq"
    export CLIMATEIQ_DB_PASSWORD="climateiq"
else
    export CLIMATEIQ_DB_HOST="${EXTERNAL_DB_HOST}"
    export CLIMATEIQ_DB_PORT="${EXTERNAL_DB_PORT}"
    export CLIMATEIQ_DB_NAME="${EXTERNAL_DB_NAME}"
    export CLIMATEIQ_DB_USER="${EXTERNAL_DB_USER}"
    export CLIMATEIQ_DB_PASSWORD="${EXTERNAL_DB_PASSWORD}"
    export CLIMATEIQ_DB_SSL="${EXTERNAL_DB_SSL}"
fi

if [ "$USE_INTERNAL_REDIS" = "true" ]; then
    export CLIMATEIQ_REDIS_URL="redis://127.0.0.1:6379/0"
else
    export CLIMATEIQ_REDIS_URL="${EXTERNAL_REDIS_URL}"
fi

cd /app
python3 -m alembic -c backend/migrations/alembic.ini upgrade head 2>/dev/null || \
    echo "[ClimateIQ] Migrations skipped (alembic.ini not found or no migrations pending)."

# =============================================================================
# Start ClimateIQ Backend
# =============================================================================

echo "[ClimateIQ] Starting ClimateIQ backend..."

# Export environment for the backend
export CLIMATEIQ_DEBUG="false"
export CLIMATEIQ_PORT="8420"
export CLIMATEIQ_LOG_LEVEL="${LOG_LEVEL}"
export CLIMATEIQ_TEMPERATURE_UNIT="${TEMPERATURE_UNIT}"
export CLIMATEIQ_HOME_ASSISTANT_URL="http://supervisor/core"
export CLIMATEIQ_HOME_ASSISTANT_TOKEN="${SUPERVISOR_TOKEN:-}"
export CLIMATEIQ_HA_ADDON_MODE="true"
export CLIMATEIQ_MQTT_AUTO_DISCOVER="${MQTT_AUTO_DISCOVER}"

# MQTT settings (from auto-discovery or empty)
export CLIMATEIQ_MQTT_BROKER="${MQTT_BROKER}"
export CLIMATEIQ_MQTT_PORT="${MQTT_PORT:-1883}"
export CLIMATEIQ_MQTT_USERNAME="${MQTT_USERNAME}"
export CLIMATEIQ_MQTT_PASSWORD="${MQTT_PASSWORD}"

# LLM API keys from options
export CLIMATEIQ_ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
export CLIMATEIQ_OPENAI_API_KEY="${OPENAI_API_KEY}"
export CLIMATEIQ_GEMINI_API_KEY="${GEMINI_API_KEY}"
export CLIMATEIQ_GROK_API_KEY="${GROK_API_KEY}"
if [ -n "$OLLAMA_URL" ]; then
    export CLIMATEIQ_OLLAMA_URL="${OLLAMA_URL}"
fi

# Secret key - generate a persistent one
SECRET_FILE="/data/climateiq/.secret_key"
if [ ! -f "$SECRET_FILE" ]; then
    mkdir -p /data/climateiq
    python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$SECRET_FILE"
fi
export CLIMATEIQ_SECRET_KEY=$(cat "$SECRET_FILE")

# Start uvicorn
python3 -m uvicorn backend.api.main:app \
    --host 127.0.0.1 \
    --port 8420 \
    --log-level "${LOG_LEVEL}" \
    --no-access-log \
    --workers 1 &
PIDS+=($!)

# Wait for backend to be ready
echo "[ClimateIQ] Waiting for backend to start..."
for i in $(seq 1 60); do
    if curl -sf http://127.0.0.1:8420/health >/dev/null 2>&1; then
        echo "[ClimateIQ] Backend ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "[ClimateIQ] ERROR: Backend failed to start within 30 seconds."
        cleanup
        exit 1
    fi
    sleep 0.5
done

echo "============================================"
echo "  ClimateIQ is running!"
echo "  Access via Home Assistant sidebar"
echo "============================================"

# =============================================================================
# Wait for any process to exit
# =============================================================================

# Keep the script alive - wait for any child to exit
wait -n "${PIDS[@]}" 2>/dev/null || true

echo "[ClimateIQ] A process exited unexpectedly. Shutting down..."
cleanup
