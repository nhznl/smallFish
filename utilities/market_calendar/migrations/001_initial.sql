CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
) STRICT;

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL,
    category TEXT NOT NULL,
    country TEXT NOT NULL,
    reference_period TEXT,
    reference_label TEXT,
    scheduled_at_utc TEXT,
    civil_date TEXT,
    session_date TEXT NOT NULL,
    original_timezone TEXT,
    time_precision TEXT NOT NULL,
    schedule_status TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    portfolio_relevance TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE event_schedule_history (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    scheduled_at_utc TEXT,
    civil_date TEXT,
    original_timezone TEXT,
    time_precision TEXT NOT NULL,
    schedule_status TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    UNIQUE (event_id, scheduled_at_utc, civil_date, time_precision, schedule_status, source_provider)
) STRICT;

CREATE TABLE event_relationships (
    event_id TEXT NOT NULL REFERENCES events(id),
    related_event_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    PRIMARY KEY (event_id, related_event_id, relationship)
) STRICT;

CREATE TABLE event_measurements (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    metric TEXT NOT NULL,
    label TEXT NOT NULL,
    value TEXT,
    numeric_value TEXT,
    unit TEXT,
    scale TEXT,
    seasonal_adjustment TEXT,
    value_kind TEXT NOT NULL,
    provider TEXT,
    observed_at_utc TEXT,
    UNIQUE (event_id, metric, value_kind, provider)
) STRICT;

CREATE TABLE event_assets (
    event_id TEXT NOT NULL REFERENCES events(id),
    asset_class TEXT NOT NULL,
    PRIMARY KEY (event_id, asset_class)
) STRICT;

CREATE TABLE event_instruments (
    event_id TEXT NOT NULL REFERENCES events(id),
    symbol TEXT NOT NULL,
    PRIMARY KEY (event_id, symbol)
) STRICT;

CREATE TABLE event_etf_exposures (
    event_id TEXT NOT NULL REFERENCES events(id),
    symbol TEXT NOT NULL,
    relationship TEXT NOT NULL,
    relevance TEXT NOT NULL,
    impact_channel TEXT NOT NULL,
    price_data_state TEXT NOT NULL,
    latest_price_session TEXT,
    PRIMARY KEY (event_id, symbol)
) STRICT;

CREATE TABLE event_source_facts (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    provider TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    parser_version TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    field_provenance_json TEXT NOT NULL,
    UNIQUE (provider, source_record_id)
) STRICT;

CREATE TABLE ingestion_runs (
    id INTEGER PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    horizon_start TEXT NOT NULL,
    horizon_end TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL
) STRICT;

CREATE TABLE source_sync_state (
    provider TEXT PRIMARY KEY,
    required INTEGER NOT NULL,
    configured INTEGER NOT NULL,
    coverage_start TEXT,
    coverage_end TEXT,
    last_attempt_utc TEXT,
    last_success_utc TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL,
    error_category TEXT,
    detail TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    payload_sha256 TEXT,
    scope TEXT NOT NULL,
    freshness_hours INTEGER
) STRICT;

CREATE TABLE importance_assessments (
    event_id TEXT NOT NULL REFERENCES events(id),
    score INTEGER NOT NULL,
    label TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    PRIMARY KEY (event_id, policy_version)
) STRICT;

CREATE TABLE strategy_assessments (
    event_id TEXT NOT NULL REFERENCES events(id),
    strategy_id TEXT NOT NULL,
    assessment TEXT NOT NULL,
    timing_relationship TEXT NOT NULL,
    benefits_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    rule_ids_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    rank_key_json TEXT NOT NULL,
    PRIMARY KEY (event_id, strategy_id, policy_version)
) STRICT;

CREATE INDEX idx_events_session ON events(session_date);

INSERT INTO schema_migrations (version, applied_at) VALUES ('001', '2026-10-04T00:00:00Z');
