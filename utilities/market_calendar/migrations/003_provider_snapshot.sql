CREATE TABLE provider_schedule_observations (
    provider TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL,
    reference_period TEXT NOT NULL,
    reference_label TEXT NOT NULL,
    scheduled_at_utc TEXT,
    civil_date TEXT NOT NULL,
    original_timezone TEXT,
    time_precision TEXT NOT NULL,
    schedule_status TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    PRIMARY KEY (provider, source_record_id)
) STRICT;

CREATE TABLE materialization_state (
    provider TEXT PRIMARY KEY,
    input_sha256 TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    horizon_start TEXT NOT NULL,
    horizon_end TEXT NOT NULL,
    materialized_at_utc TEXT NOT NULL
) STRICT;

ALTER TABLE source_sync_state DROP COLUMN materialization_sha256;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('003', '2026-10-05T00:00:00Z');
