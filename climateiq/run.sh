#!/usr/bin/env bash
# ClimateIQ Home Assistant Add-on Startup Script
# Lightweight: reads HA options, sets env vars, starts uvicorn directly.
# Requires external TimescaleDB (pg18) and external Redis.
set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

CONFIG_PATH="/data/options.json"
DATA_DIR="/data/climateiq"

# Read options from HA add-on config
if [ -f "$CONFIG_PATH" ]; then
    LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG_PATH")
    TEMPERATURE_UNIT=$(jq -r '.temperature_unit // "F"' "$CONFIG_PATH")

    # Database (required)
    DB_HOST=$(jq -r '.db_host // ""' "$CONFIG_PATH")
    DB_PORT=$(jq -r '.db_port // 5432' "$CONFIG_PATH")
    DB_NAME=$(jq -r '.db_name // "climateiq"' "$CONFIG_PATH")
    DB_USER=$(jq -r '.db_user // "climateiq"' "$CONFIG_PATH")
    DB_PASSWORD=$(jq -r '.db_password // ""' "$CONFIG_PATH")

    # Redis (required)
    REDIS_URL=$(jq -r '.redis_url // ""' "$CONFIG_PATH")

    # LLM API keys (optional)
    ANTHROPIC_API_KEY=$(jq -r '.anthropic_api_key // ""' "$CONFIG_PATH")
    OPENAI_API_KEY=$(jq -r '.openai_api_key // ""' "$CONFIG_PATH")
    GEMINI_API_KEY=$(jq -r '.gemini_api_key // ""' "$CONFIG_PATH")
    GROK_API_KEY=$(jq -r '.grok_api_key // ""' "$CONFIG_PATH")
    OLLAMA_URL=$(jq -r '.ollama_url // ""' "$CONFIG_PATH")
else
    LOG_LEVEL="info"
    TEMPERATURE_UNIT="F"
    DB_HOST=""
    DB_PORT="5432"
    DB_NAME="climateiq"
    DB_USER="climateiq"
    DB_PASSWORD=""
    REDIS_URL=""
    ANTHROPIC_API_KEY=""
    OPENAI_API_KEY=""
    GEMINI_API_KEY=""
    GROK_API_KEY=""
    OLLAMA_URL=""
fi

echo "============================================"
echo "  ClimateIQ Home Assistant Add-on v0.2.4"
echo "============================================"
echo "Log level:          ${LOG_LEVEL}"
echo "Temperature unit:   ${TEMPERATURE_UNIT}"
echo "Database host:      ${DB_HOST:-<not set>}"
echo "Redis URL:          ${REDIS_URL:-<not set>}"
echo "============================================"

# =============================================================================
# Validate required configuration
# =============================================================================

if [ -z "$DB_HOST" ] || [ -z "$DB_PASSWORD" ]; then
    echo "[ClimateIQ] ERROR: Database configuration is incomplete."
    echo "[ClimateIQ] Set db_host and db_password in the add-on configuration."
    exit 1
fi

if [ -z "$REDIS_URL" ]; then
    echo "[ClimateIQ] ERROR: Redis URL is not configured."
    echo "[ClimateIQ] Set redis_url in the add-on configuration."
    exit 1
fi

# =============================================================================
# Network connectivity check
# =============================================================================

echo "[ClimateIQ] Checking network connectivity to ${DB_HOST}:${DB_PORT}..."

# Python-level DNS resolution test (same runtime as the app)
python3 -c "
import socket
host = '${DB_HOST}'
port = ${DB_PORT}
print(f'  getaddrinfo({host}, {port}):')
try:
    result = socket.getaddrinfo(host, port)
    for r in result:
        print(f'    {r}')
except Exception as e:
    print(f'    FAILED: {e}')
print(f'  /etc/resolv.conf:')
try:
    with open('/etc/resolv.conf') as f:
        for line in f:
            print(f'    {line.rstrip()}')
except Exception as e:
    print(f'    {e}')
print(f'  /etc/hosts entries for {host}:')
try:
    with open('/etc/hosts') as f:
        for line in f:
            if host in line:
                print(f'    {line.rstrip()}')
except Exception:
    pass
"

if nc -z -w3 "$DB_HOST" "$DB_PORT" 2>/dev/null; then
    echo "[ClimateIQ] Database host is reachable via nc."
else
    echo "[ClimateIQ] WARNING: Cannot reach ${DB_HOST}:${DB_PORT} via nc"
    echo "[ClimateIQ] Network interfaces:"
    ip addr 2>/dev/null | grep 'inet ' || echo "  (ip addr not available)"
    echo "[ClimateIQ] Default route:"
    ip route 2>/dev/null | grep default || echo "  (no default route)"
fi

# =============================================================================
# Signal handling for graceful shutdown
# =============================================================================

UVICORN_PID=""

cleanup() {
    echo "[ClimateIQ] Shutting down gracefully..."
    if [ -n "$UVICORN_PID" ] && kill -0 "$UVICORN_PID" 2>/dev/null; then
        kill -TERM "$UVICORN_PID" 2>/dev/null || true
        wait "$UVICORN_PID" 2>/dev/null || true
    fi
    echo "[ClimateIQ] Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP

# =============================================================================
# Persistent secret key
# =============================================================================

SECRET_FILE="${DATA_DIR}/.secret_key"
if [ ! -f "$SECRET_FILE" ]; then
    mkdir -p "$DATA_DIR"
    python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$SECRET_FILE"
fi

# =============================================================================
# Export environment for the backend
# =============================================================================

# Core
export CLIMATEIQ_DEBUG="false"
export CLIMATEIQ_PORT="8099"
export CLIMATEIQ_HOST="0.0.0.0"
export CLIMATEIQ_LOG_LEVEL="${LOG_LEVEL}"
export CLIMATEIQ_TEMPERATURE_UNIT="${TEMPERATURE_UNIT}"
export CLIMATEIQ_SECRET_KEY=$(cat "$SECRET_FILE")

# Database
export CLIMATEIQ_DB_HOST="${DB_HOST}"
export CLIMATEIQ_DB_PORT="${DB_PORT}"
export CLIMATEIQ_DB_NAME="${DB_NAME}"
export CLIMATEIQ_DB_USER="${DB_USER}"
export CLIMATEIQ_DB_PASSWORD="${DB_PASSWORD}"

# Redis
export CLIMATEIQ_REDIS_URL="${REDIS_URL}"

# Home Assistant integration
export CLIMATEIQ_HOME_ASSISTANT_URL="http://supervisor/core"
export CLIMATEIQ_HOME_ASSISTANT_TOKEN="${SUPERVISOR_TOKEN:-}"
export CLIMATEIQ_HA_ADDON_MODE="true"

# LLM API keys
export CLIMATEIQ_ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
export CLIMATEIQ_OPENAI_API_KEY="${OPENAI_API_KEY}"
export CLIMATEIQ_GEMINI_API_KEY="${GEMINI_API_KEY}"
export CLIMATEIQ_GROK_API_KEY="${GROK_API_KEY}"
if [ -n "$OLLAMA_URL" ]; then
    export CLIMATEIQ_OLLAMA_URL="${OLLAMA_URL}"
fi

# =============================================================================
# Start ClimateIQ backend (serves both API and frontend SPA)
# =============================================================================

echo "[ClimateIQ] Starting ClimateIQ backend on port 8099..."
cd /app

python3 -m uvicorn backend.api.main:app \
    --host 0.0.0.0 \
    --port 8099 \
    --log-level "${LOG_LEVEL}" \
    --no-access-log \
    --workers 1 &
UVICORN_PID=$!

# Wait for backend to be ready
echo "[ClimateIQ] Waiting for backend to start..."
for i in $(seq 1 60); do
    if curl -sf http://127.0.0.1:8099/health >/dev/null 2>&1; then
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

# Keep the script alive — wait for uvicorn to exit
wait "$UVICORN_PID"
