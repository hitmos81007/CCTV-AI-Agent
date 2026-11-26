-- Phase 2 MVP schema, same-case integrity, owner RLS, Realtime case_events.
-- Forward-only migration extending 001..003 without destructive resets.

-- 1. Cases: ownership, title, authorization, update timestamp
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES auth.users(id) ON DELETE RESTRICT;
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS authorized_at TIMESTAMPTZ;
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS cases_owner_created_idx ON public.cases(owner_id, created_at);

-- 2. Videos: upload idempotency, source label, storage metadata, nullable capture time
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS upload_key TEXT;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS source_label TEXT;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS storage_kind TEXT;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS media_status TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE public.videos ALTER COLUMN recorded_at DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS videos_case_upload_key_uidx
    ON public.videos(case_id, upload_key) WHERE upload_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS videos_case_idx ON public.videos(case_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'videos_case_source_unique') THEN
        ALTER TABLE public.videos ADD CONSTRAINT videos_case_source_unique UNIQUE (case_id, video_source_id);
    END IF;
END $$;

-- 3. Recordings: unique source video reference for MVP uploads
CREATE UNIQUE INDEX IF NOT EXISTS recordings_video_source_uidx
    ON public.recordings(video_source_id) WHERE video_source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS recordings_case_video_idx ON public.recordings(case_id, video_source_id);

-- 4. Orchestration runs: start key, status, immutable input snapshot, timings
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS start_key TEXT;
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'planning';
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS input_video_ids JSONB;
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS error_code TEXT;
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS orchestration_runs_case_idx ON public.orchestration_runs(case_id);
CREATE UNIQUE INDEX IF NOT EXISTS orchestration_runs_case_start_key_uidx
    ON public.orchestration_runs(case_id, start_key) WHERE start_key IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orchestration_runs_case_id_unique') THEN
        ALTER TABLE public.orchestration_runs ADD CONSTRAINT orchestration_runs_case_id_unique UNIQUE (case_id, id);
    END IF;
END $$;

-- 5. Investigation jobs: run link, heartbeat, start/finish timestamps, same-case FKs
ALTER TABLE public.investigation_jobs ADD COLUMN IF NOT EXISTS run_id TEXT REFERENCES public.orchestration_runs(id);
ALTER TABLE public.investigation_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE public.investigation_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE public.investigation_jobs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS investigation_jobs_run_video_uidx
    ON public.investigation_jobs(run_id, video_source_id)
    WHERE run_id IS NOT NULL AND video_source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS investigation_jobs_status_next_run_idx
    ON public.investigation_jobs(status, next_run_at);
CREATE INDEX IF NOT EXISTS investigation_jobs_case_run_status_idx
    ON public.investigation_jobs(case_id, run_id, status);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'investigation_jobs_case_run_fk') THEN
        ALTER TABLE public.investigation_jobs
            ADD CONSTRAINT investigation_jobs_case_run_fk
            FOREIGN KEY (case_id, run_id) REFERENCES public.orchestration_runs(case_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'investigation_jobs_case_video_fk') THEN
        ALTER TABLE public.investigation_jobs
            ADD CONSTRAINT investigation_jobs_case_video_fk
            FOREIGN KEY (case_id, video_source_id) REFERENCES public.videos(case_id, video_source_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'investigation_jobs_case_id_unique') THEN
        ALTER TABLE public.investigation_jobs
            ADD CONSTRAINT investigation_jobs_case_id_unique UNIQUE (case_id, id);
    END IF;
END $$;

-- 6. Orchestration tasks: unique job_id
CREATE UNIQUE INDEX IF NOT EXISTS orchestration_tasks_job_id_uidx ON public.orchestration_tasks(job_id);

-- 7. Sightings: job link, candidate key, private frame key, authenticated reviewer, nullable occurred_at
ALTER TABLE public.sightings ADD COLUMN IF NOT EXISTS job_id TEXT REFERENCES public.investigation_jobs(id);
ALTER TABLE public.sightings ADD COLUMN IF NOT EXISTS candidate_key TEXT;
ALTER TABLE public.sightings ADD COLUMN IF NOT EXISTS frame_storage_key TEXT;
ALTER TABLE public.sightings ADD COLUMN IF NOT EXISTS reviewer_user_id UUID REFERENCES auth.users(id) ON DELETE RESTRICT;
ALTER TABLE public.sightings ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE public.sightings ALTER COLUMN occurred_at DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS sightings_job_candidate_uidx
    ON public.sightings(job_id, candidate_key)
    WHERE job_id IS NOT NULL AND candidate_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS sightings_case_review_created_idx
    ON public.sightings(case_id, review_status, created_at);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sightings_case_video_fk') THEN
        ALTER TABLE public.sightings
            ADD CONSTRAINT sightings_case_video_fk
            FOREIGN KEY (case_id, video_source_id) REFERENCES public.videos(case_id, video_source_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sightings_case_job_fk') THEN
        ALTER TABLE public.sightings
            ADD CONSTRAINT sightings_case_job_fk
            FOREIGN KEY (case_id, job_id) REFERENCES public.investigation_jobs(case_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sightings_case_id_unique') THEN
        ALTER TABLE public.sightings
            ADD CONSTRAINT sightings_case_id_unique UNIQUE (case_id, id);
    END IF;
END $$;

-- 8. Finding reviews: append-only authenticated review events
CREATE TABLE IF NOT EXISTS public.finding_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sighting_id TEXT NOT NULL REFERENCES public.sightings(id) ON DELETE RESTRICT,
    case_id TEXT NOT NULL REFERENCES public.cases(case_id) ON DELETE RESTRICT,
    reviewer_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('confirmed_candidate', 'not_relevant')),
    note TEXT,
    request_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sighting_id, request_key),
    CONSTRAINT finding_reviews_case_sighting_fk
        FOREIGN KEY (case_id, sighting_id) REFERENCES public.sightings(case_id, id)
);

CREATE INDEX IF NOT EXISTS finding_reviews_sighting_created_idx
    ON public.finding_reviews(sighting_id, created_at);
CREATE INDEX IF NOT EXISTS finding_reviews_case_idx
    ON public.finding_reviews(case_id);

-- 9. Case events: minimal sanitized Realtime notification table
CREATE TABLE IF NOT EXISTS public.case_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES public.cases(case_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('case_changed', 'video_changed', 'job_changed', 'finding_changed', 'review_changed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS case_events_case_id_idx ON public.case_events(case_id, id);

-- 10. Enable RLS on new tables
ALTER TABLE public.finding_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_events ENABLE ROW LEVEL SECURITY;

-- 11. Least-privilege grants: revoke public/anon/authenticated, grant safe column SELECT only
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, anon, authenticated;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT USAGE ON SCHEMA public TO authenticated;

        GRANT SELECT (case_id, owner_id, title, description, clues_json, authorized_at, created_at, updated_at)
            ON public.cases TO authenticated;

        GRANT SELECT (video_source_id, case_id, original_name, source_label, recorded_at, sha256, byte_size, duration_seconds, media_status, created_at)
            ON public.videos TO authenticated;

        GRANT SELECT (id, case_id, camera_id, jurisdiction, source_type, video_source_id, recorded_at, status, created_at)
            ON public.recordings TO authenticated;

        GRANT SELECT (case_id, status, updated_at)
            ON public.investigation_states TO authenticated;

        GRANT SELECT (id, case_id, run_id, video_source_id, worker_id, status, attempts, heartbeat_at, started_at, finished_at, created_at, updated_at)
            ON public.investigation_jobs TO authenticated;

        GRANT SELECT (id, case_id, job_id, worker_id, video_source_id, occurred_at, video_offset_seconds, track_id, vehicle_type, vehicle_color, subject_type, visual_attributes_json, detector_confidence, clue_match_score, evidence_sha256, frame_sha256, review_status, reviewer_user_id, reviewed_at, review_note, created_at)
            ON public.sightings TO authenticated;

        GRANT SELECT (id, sighting_id, case_id, reviewer_user_id, decision, note, created_at)
            ON public.finding_reviews TO authenticated;

        GRANT SELECT (id, case_id, kind, created_at)
            ON public.case_events TO authenticated;
    END IF;
END $$;

-- 12. Owner-scoped RLS policies (SELECT only; all mutations happen through verified backend)
DROP POLICY IF EXISTS cases_owner_select ON public.cases;
CREATE POLICY cases_owner_select ON public.cases
    FOR SELECT TO authenticated
    USING (owner_id IS NOT NULL AND owner_id = auth.uid());

DROP POLICY IF EXISTS videos_owner_select ON public.videos;
CREATE POLICY videos_owner_select ON public.videos
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = videos.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS recordings_owner_select ON public.recordings;
CREATE POLICY recordings_owner_select ON public.recordings
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = recordings.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS investigation_states_owner_select ON public.investigation_states;
CREATE POLICY investigation_states_owner_select ON public.investigation_states
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = investigation_states.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS investigation_jobs_owner_select ON public.investigation_jobs;
CREATE POLICY investigation_jobs_owner_select ON public.investigation_jobs
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = investigation_jobs.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS sightings_owner_select ON public.sightings;
CREATE POLICY sightings_owner_select ON public.sightings
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = sightings.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS finding_reviews_owner_select ON public.finding_reviews;
CREATE POLICY finding_reviews_owner_select ON public.finding_reviews
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = finding_reviews.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS case_events_owner_select ON public.case_events;
CREATE POLICY case_events_owner_select ON public.case_events
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = case_events.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

-- 13. Add minimal sanitized case_events to Supabase Realtime publication
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'case_events'
        ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.case_events;
        END IF;
    END IF;
END $$;

INSERT INTO public.schema_migrations(version, applied_at)
VALUES (4, CURRENT_TIMESTAMP::text)
ON CONFLICT(version) DO NOTHING;
