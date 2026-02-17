-- ClimateIQ Database Initialization Script
-- Runs on first container start

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgvector";

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create hypertable for sensor readings (time-series optimization)
-- This will be applied after SQLAlchemy creates the tables

-- Function to convert sensor_readings to hypertable
CREATE OR REPLACE FUNCTION create_hypertables()
RETURNS void AS $$
BEGIN
    -- Check if sensor_readings exists and is not already a hypertable
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'sensor_readings'
    ) AND NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'sensor_readings'
    ) THEN
        PERFORM create_hypertable('sensor_readings', 'recorded_at', 
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE
        );
        
        -- Add compression policy (compress chunks older than 7 days)
        ALTER TABLE sensor_readings SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'sensor_id'
        );
        
        SELECT add_compression_policy('sensor_readings', INTERVAL '7 days');
        
        -- Add retention policy (drop data older than 1 year)
        SELECT add_retention_policy('sensor_readings', INTERVAL '1 year');
    END IF;
    
    -- Check if device_actions exists
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'device_actions'
    ) AND NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'device_actions'
    ) THEN
        PERFORM create_hypertable('device_actions', 'created_at', 
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        );
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Create indexes for common queries (will be applied after tables exist)
CREATE OR REPLACE FUNCTION create_indexes()
RETURNS void AS $$
BEGIN
    -- Sensor readings indexes
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'sensor_readings') THEN
        CREATE INDEX IF NOT EXISTS idx_sensor_readings_sensor_time 
            ON sensor_readings (sensor_id, recorded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sensor_readings_time 
            ON sensor_readings (recorded_at DESC);
    END IF;
    
    -- Device actions indexes
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'device_actions') THEN
        CREATE INDEX IF NOT EXISTS idx_device_actions_device_time 
            ON device_actions (device_id, created_at DESC);
    END IF;
    
    -- Conversations indexes
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'conversations') THEN
        CREATE INDEX IF NOT EXISTS idx_conversations_session 
            ON conversations (session_id, created_at DESC);
    END IF;
    
    -- Schedules indexes
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'schedules') THEN
        CREATE INDEX IF NOT EXISTS idx_schedules_zone 
            ON schedules (zone_id) WHERE zone_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_schedules_enabled 
            ON schedules (is_enabled) WHERE is_enabled = TRUE;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Create continuous aggregates for hourly/daily stats
CREATE OR REPLACE FUNCTION create_continuous_aggregates()
RETURNS void AS $$
BEGIN
    -- Hourly sensor averages
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'sensor_readings') THEN
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_hourly
        WITH (timescaledb.continuous) AS
        SELECT 
            time_bucket('1 hour', recorded_at) AS bucket,
            sensor_id,
            AVG(temperature_c) AS avg_temperature,
            AVG(humidity) AS avg_humidity,
            COUNT(*) AS reading_count
        FROM sensor_readings
        WHERE temperature_c IS NOT NULL OR humidity IS NOT NULL
        GROUP BY bucket, sensor_id
        WITH NO DATA;
        
        -- Add refresh policy
        SELECT add_continuous_aggregate_policy('sensor_readings_hourly',
            start_offset => INTERVAL '3 hours',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour'
        );
        
        -- Daily sensor averages
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_daily
        WITH (timescaledb.continuous) AS
        SELECT 
            time_bucket('1 day', recorded_at) AS bucket,
            sensor_id,
            AVG(temperature_c) AS avg_temperature,
            MIN(temperature_c) AS min_temperature,
            MAX(temperature_c) AS max_temperature,
            AVG(humidity) AS avg_humidity,
            COUNT(*) AS reading_count
        FROM sensor_readings
        WHERE temperature_c IS NOT NULL
        GROUP BY bucket, sensor_id
        WITH NO DATA;
        
        SELECT add_continuous_aggregate_policy('sensor_readings_daily',
            start_offset => INTERVAL '3 days',
            end_offset => INTERVAL '1 day',
            schedule_interval => INTERVAL '1 day'
        );
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Note: These functions will be called after SQLAlchemy creates the base tables
-- You can call them manually or set up a post-init script

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO climateiq;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO climateiq;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO climateiq;
