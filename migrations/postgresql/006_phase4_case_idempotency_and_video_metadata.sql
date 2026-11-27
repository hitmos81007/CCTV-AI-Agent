-- Phase 4: Case creation idempotency, video media metadata, and nullable recording timestamps.
-- Forward-only migration extending 001..005 without modifying applied migrations.

-- 1. Cases: server-scoped idempotency key to prevent duplicate draft creation on retries
ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS cases_owner_idempotency_uidx
    ON public.cases(owner_id, idempotency_key)
    WHERE owner_id IS NOT NULL AND idempotency_key IS NOT NULL;

-- 2. Videos: optional extracted technical media properties
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS width INTEGER;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS height INTEGER;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS fps DOUBLE PRECISION;
ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS codec TEXT;

-- 3. Recordings: allow NULL recorded_at when capture time is unknown
ALTER TABLE public.recordings ALTER COLUMN recorded_at DROP NOT NULL;

INSERT INTO public.schema_migrations(version, applied_at)
VALUES (6, CURRENT_TIMESTAMP::text)
ON CONFLICT(version) DO NOTHING;
