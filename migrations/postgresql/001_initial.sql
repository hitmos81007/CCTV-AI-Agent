-- CCTV investigation schema. Apply on a dedicated backend database.

            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                initial_zone TEXT NOT NULL,
                neighbors_json TEXT NOT NULL DEFAULT '{}',
                clues_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS videos (
                video_source_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                jurisdiction TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS videos_zone_time ON videos(case_id, jurisdiction, recorded_at);
            CREATE TABLE IF NOT EXISTS sightings (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                worker_id TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                video_source_id TEXT NOT NULL REFERENCES videos(video_source_id),
                occurred_at TEXT NOT NULL,
                video_offset_seconds DOUBLE PRECISION NOT NULL,
                track_id INTEGER,
                vehicle_type TEXT NOT NULL,
                vehicle_color TEXT,
                subject_type TEXT NOT NULL DEFAULT 'vehicle',
                visual_attributes_json TEXT NOT NULL DEFAULT '[]',
                integrity_version INTEGER NOT NULL DEFAULT 1,
                clue_json TEXT,
                frame_path TEXT,
                frame_sha256 TEXT,
                detector_confidence DOUBLE PRECISION NOT NULL,
                clue_match_score DOUBLE PRECISION,
                evidence_sha256 TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending',
                reviewer TEXT,
                review_note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sightings_case_time ON sightings(case_id, occurred_at);
            CREATE TABLE IF NOT EXISTS followups (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                source_sighting_id TEXT NOT NULL REFERENCES sightings(id),
                target_zone TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                video_source_id TEXT REFERENCES videos(video_source_id),
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                UNIQUE(source_sighting_id, target_zone)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id BIGSERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                entry_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );


        CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cameras (
            camera_id TEXT PRIMARY KEY, jurisdiction TEXT NOT NULL, name TEXT NOT NULL,
            latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, source_type TEXT NOT NULL, source_uri TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recordings (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            camera_id TEXT NOT NULL, jurisdiction TEXT NOT NULL, source_type TEXT NOT NULL,
            video_source_id TEXT REFERENCES videos(video_source_id),
            source_uri TEXT, recorded_at TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investigation_jobs (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            jurisdiction TEXT NOT NULL, video_source_id TEXT REFERENCES videos(video_source_id),
            worker_id TEXT NOT NULL, status TEXT NOT NULL, error_detail TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investigation_states (
            case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
            status TEXT NOT NULL, updated_at TEXT NOT NULL
        );
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

INSERT INTO schema_migrations(version, applied_at) VALUES(1, CURRENT_TIMESTAMP::text) ON CONFLICT(version) DO NOTHING;

-- The backend connects as the table owner. Supabase Data API roles receive no
-- policies here, so these tables are not directly exposed to browser clients.
ALTER TABLE cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE sightings ENABLE ROW LEVEL SECURITY;
ALTER TABLE followups ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cameras ENABLE ROW LEVEL SECURITY;
ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE investigation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE investigation_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestration_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestration_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_metadata ENABLE ROW LEVEL SECURITY;
INSERT INTO recordings(id,case_id,camera_id,jurisdiction,source_type,video_source_id,recorded_at,status,created_at)
SELECT video_source_id,case_id,camera_id,jurisdiction,'upload',video_source_id,recorded_at,'ready',created_at FROM videos v
WHERE NOT EXISTS (SELECT 1 FROM recordings r WHERE r.video_source_id=v.video_source_id)
ON CONFLICT(id) DO NOTHING;
