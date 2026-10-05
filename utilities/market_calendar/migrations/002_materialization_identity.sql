ALTER TABLE source_sync_state ADD COLUMN materialization_sha256 TEXT;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('002', '2026-10-04T00:00:00Z');
