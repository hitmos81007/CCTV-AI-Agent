-- Phase 5: Orchestration run constraints, job lifecycle checks, and failure detail persistence.
-- Forward-only migration extending 001..006 without modifying applied migrations.

-- 1. Orchestration runs: explicit failure detail, job-creation flag, and valid status constraints
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS error_detail TEXT;
ALTER TABLE public.orchestration_runs ADD COLUMN IF NOT EXISTS has_jobs BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE public.orchestration_runs r
SET has_jobs = TRUE
WHERE EXISTS (SELECT 1 FROM public.investigation_jobs j WHERE j.run_id = r.id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orchestration_runs_status_check') THEN
        ALTER TABLE public.orchestration_runs
            ADD CONSTRAINT orchestration_runs_status_check
            CHECK (status IN ('planning', 'queued', 'running', 'completed', 'partially_completed', 'failed'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orchestration_runs_snapshot_array_check') THEN
        ALTER TABLE public.orchestration_runs
            ADD CONSTRAINT orchestration_runs_snapshot_array_check
            CHECK (input_video_ids IS NULL OR jsonb_typeof(input_video_ids) = 'array');
    END IF;
END $$;

-- Enforce one official MVP run per case; allow retry ONLY for failed planning runs with zero jobs
CREATE UNIQUE INDEX IF NOT EXISTS orchestration_runs_active_case_uidx
    ON public.orchestration_runs(case_id)
    WHERE NOT (status = 'failed' AND has_jobs = FALSE);

-- 2. Investigation jobs: valid status constraint
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'investigation_jobs_status_check') THEN
        ALTER TABLE public.investigation_jobs
            ADD CONSTRAINT investigation_jobs_status_check
            CHECK (status IN ('queued', 'running', 'retrying', 'completed', 'failed'));
    END IF;
END $$;

-- 3. Investigation states: valid lifecycle status constraint
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'investigation_states_status_check') THEN
        ALTER TABLE public.investigation_states
            ADD CONSTRAINT investigation_states_status_check
            CHECK (status IN (
                'draft', 'planning', 'queued', 'running', 'processing',
                'completed', 'partially_completed', 'failed',
                'open', 'searching', 'review', 'closed'
            ));
    END IF;
END $$;

INSERT INTO public.schema_migrations(version, applied_at)
VALUES (7, CURRENT_TIMESTAMP::text)
ON CONFLICT(version) DO NOTHING;
