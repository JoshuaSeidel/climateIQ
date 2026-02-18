# ClimateIQ Home Assistant Add-on Dockerfile
# Lightweight: Python backend + pre-built frontend, external DB & Redis required
ARG BUILD_FROM=python:3.13-alpine

# =============================================================================
# Stage 1: Build frontend
# =============================================================================
FROM node:24-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
ENV VITE_BASE_PATH="./"
RUN npm run build

# =============================================================================
# Stage 2: Final image
# =============================================================================
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    libpq \
    curl \
    jq \
    bash

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy backend source and version file
COPY backend/ ./backend/
COPY VERSION ./

# Copy frontend build
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

# Copy entrypoint
COPY climateiq/run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
    io.hass.name="ClimateIQ" \
    io.hass.description="AI-Powered Intelligent Climate Management" \
    io.hass.arch="amd64|aarch64" \
    io.hass.type="addon" \
    io.hass.version="0.5.0"

ENTRYPOINT ["/run.sh"]
