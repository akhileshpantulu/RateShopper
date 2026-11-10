CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'platform_enum') THEN
        CREATE TYPE platform_enum AS ENUM ('trip', 'booking', 'google_hotels', 'agoda');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS hotel_master (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    city VARCHAR(120) NOT NULL,
    country VARCHAR(120) NOT NULL,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hotel_master_city ON hotel_master(city);

CREATE TABLE IF NOT EXISTS hotel_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotel_id UUID NOT NULL REFERENCES hotel_master(id) ON DELETE CASCADE,
    platform platform_enum NOT NULL,
    platform_hotel_id VARCHAR(255) NOT NULL,
    platform_hotel_name VARCHAR(255),
    platform_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_platform_hotel_id UNIQUE(platform, platform_hotel_id)
);

CREATE INDEX IF NOT EXISTS idx_hotel_mapping_hotel_id ON hotel_mapping(hotel_id);
CREATE INDEX IF NOT EXISTS idx_hotel_mapping_platform ON hotel_mapping(platform);

CREATE TABLE IF NOT EXISTS rate_snapshot (
    id BIGSERIAL PRIMARY KEY,
    mapping_id UUID NOT NULL REFERENCES hotel_mapping(id) ON DELETE CASCADE,
    platform platform_enum NOT NULL,
    room_name VARCHAR(255) NOT NULL,
    meal_plan VARCHAR(255),
    cancellation_policy TEXT,
    currency CHAR(3) NOT NULL,
    base_price NUMERIC(12, 2) NOT NULL,
    tax_fee NUMERIC(12, 2) NOT NULL,
    final_price NUMERIC(12, 2) NOT NULL,
    checkin DATE NOT NULL,
    checkout DATE NOT NULL,
    adults INT NOT NULL,
    children INT NOT NULL,
    source_url TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    scraped_at TIMESTAMPTZ NOT NULL,
    rate_hash VARCHAR(64) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rate_snapshot_mapping_id ON rate_snapshot(mapping_id);
CREATE INDEX IF NOT EXISTS idx_rate_snapshot_platform ON rate_snapshot(platform);
CREATE INDEX IF NOT EXISTS idx_rate_snapshot_dates ON rate_snapshot(checkin, checkout);
CREATE INDEX IF NOT EXISTS idx_rate_snapshot_scraped_at ON rate_snapshot(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_rate_snapshot_rate_hash ON rate_snapshot(rate_hash);

CREATE TABLE IF NOT EXISTS crawl_job_log (
    id BIGSERIAL PRIMARY KEY,
    platform platform_enum NOT NULL,
    target_count INT NOT NULL,
    success_count INT NOT NULL DEFAULT 0,
    failed_count INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_ms INT
);

CREATE INDEX IF NOT EXISTS idx_crawl_job_log_platform ON crawl_job_log(platform);
CREATE INDEX IF NOT EXISTS idx_crawl_job_log_started_at ON crawl_job_log(started_at DESC);
