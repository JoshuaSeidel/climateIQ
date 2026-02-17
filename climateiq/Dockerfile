# ClimateIQ Home Assistant Add-on Dockerfile
# Single-container deployment with embedded PostgreSQL, Redis, and Nginx
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.18

# =============================================================================
# Stage 1: Build frontend
# =============================================================================
FROM node:24-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
# Build with relative base path so ingress prefix works
ENV VITE_BASE_PATH="./"
RUN npm run build

# =============================================================================
# Stage 2: Final add-on image
# =============================================================================
FROM ${BUILD_FROM}

# Install system dependencies
RUN apk add --no-cache \
    nginx \
    postgresql16 \
    postgresql16-contrib \
    redis \
    build-base \
    libpq-dev \
    libffi-dev \
    curl \
    jq \
    bash \
    su-exec

# Create directories
RUN mkdir -p \
    /app \
    /data/postgresql \
    /data/redis \
    /data/climateiq \
    /run/nginx \
    /run/postgresql \
    /var/log/nginx \
    /var/log/climateiq \
    /var/log/postgresql \
    /var/log/redis

# Install Python dependencies
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Copy backend source and version file
COPY backend/ /app/backend/
COPY VERSION /app/

# Copy frontend build
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

# Copy add-on specific files
COPY climateiq/run.sh /run.sh
COPY climateiq/nginx.conf /etc/nginx/http.d/climateiq.conf
COPY climateiq/rootfs/ /

# Make scripts executable
RUN chmod a+x /run.sh && \
    chmod a+x /etc/services.d/*/run 2>/dev/null || true && \
    chmod a+x /etc/services.d/*/finish 2>/dev/null || true

# Initialize PostgreSQL data directory
RUN mkdir -p /data/postgresql/data && \
    chown -R postgres:postgres /data/postgresql /run/postgresql /var/log/postgresql && \
    su-exec postgres initdb -D /data/postgresql/data --auth=trust --encoding=UTF8 2>/dev/null || true

# Configure PostgreSQL for local-only connections
RUN if [ -f /data/postgresql/data/postgresql.conf ]; then \
      echo "listen_addresses = '127.0.0.1'" >> /data/postgresql/data/postgresql.conf && \
      echo "port = 5432" >> /data/postgresql/data/postgresql.conf && \
      echo "max_connections = 20" >> /data/postgresql/data/postgresql.conf && \
      echo "shared_buffers = 64MB" >> /data/postgresql/data/postgresql.conf && \
      echo "work_mem = 4MB" >> /data/postgresql/data/postgresql.conf && \
      echo "logging_collector = off" >> /data/postgresql/data/postgresql.conf; \
    fi

# Configure Redis for local-only connections
RUN echo "bind 127.0.0.1" > /etc/redis-climateiq.conf && \
    echo "port 6379" >> /etc/redis-climateiq.conf && \
    echo "daemonize no" >> /etc/redis-climateiq.conf && \
    echo "dir /data/redis" >> /etc/redis-climateiq.conf && \
    echo "appendonly yes" >> /etc/redis-climateiq.conf && \
    echo "maxmemory 64mb" >> /etc/redis-climateiq.conf && \
    echo "maxmemory-policy allkeys-lru" >> /etc/redis-climateiq.conf && \
    echo "save 900 1" >> /etc/redis-climateiq.conf && \
    echo "save 300 10" >> /etc/redis-climateiq.conf

# Remove default nginx config
RUN rm -f /etc/nginx/http.d/default.conf

# Set working directory
WORKDIR /app

# Labels for HA add-on
LABEL \
    io.hass.name="ClimateIQ" \
    io.hass.description="AI-Powered Intelligent Climate Management" \
    io.hass.arch="amd64|aarch64" \
    io.hass.type="addon" \
    io.hass.version="0.1.0"

ENTRYPOINT ["/run.sh"]
