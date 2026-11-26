CREATE TABLE IF NOT EXISTS orchestration_runs (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
    state_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orchestration_tasks (
    run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
    video_source_id TEXT NOT NULL REFERENCES videos(video_source_id),
    job_id TEXT NOT NULL REFERENCES investigation_jobs(id),
    PRIMARY KEY(run_id,video_source_id)
);
CREATE TABLE IF NOT EXISTS video_metadata (
    video_source_id TEXT PRIMARY KEY REFERENCES videos(video_source_id),
    metadata_json TEXT NOT NULL
);
ALTER TABLE orchestration_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestration_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_metadata ENABLE ROW LEVEL SECURITY;
INSERT INTO recordings(id,case_id,camera_id,jurisdiction,source_type,video_source_id,recorded_at,status,created_at)
SELECT video_source_id,case_id,camera_id,jurisdiction,'upload',video_source_id,recorded_at,'ready',created_at FROM videos v
WHERE NOT EXISTS (SELECT 1 FROM recordings r WHERE r.video_source_id=v.video_source_id)
ON CONFLICT(id) DO NOTHING;
INSERT INTO schema_migrations(version, applied_at) VALUES(3, CURRENT_TIMESTAMP::text) ON CONFLICT(version) DO NOTHING;
