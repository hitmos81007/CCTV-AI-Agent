ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS lease_token TEXT;
ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TEXT;
ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS next_run_at TEXT;
ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS request_json TEXT;
INSERT INTO schema_migrations(version, applied_at) VALUES(2, CURRENT_TIMESTAMP::text) ON CONFLICT(version) DO NOTHING;
