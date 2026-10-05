CREATE TABLE provider_snapshot_state (
    provider TEXT PRIMARY KEY,
    parser_version TEXT NOT NULL,
    source_url TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT
) STRICT;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('004', '2026-10-05T00:00:00Z');
