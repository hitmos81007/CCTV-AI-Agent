-- Phase 3: Officer verification, district administration, and Phase 2 audit constraints
-- Forward-only migration (version 5). Never edit already-applied migrations 001..004.

-- 1. Phase 2 audit follow-ups: same-case recording FK and orchestration_tasks video FK
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'recordings_case_video_fk') THEN
        ALTER TABLE public.recordings
            ADD CONSTRAINT recordings_case_video_fk
            FOREIGN KEY (case_id, video_source_id) REFERENCES public.videos(case_id, video_source_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orchestration_tasks_video_fk') THEN
        ALTER TABLE public.orchestration_tasks
            ADD CONSTRAINT orchestration_tasks_video_fk
            FOREIGN KEY (video_source_id) REFERENCES public.videos(video_source_id);
    END IF;
END $$;

-- 2. Controlled districts registry
CREATE TABLE IF NOT EXISTS public.districts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.districts(id, name, is_active) VALUES
    ('central-metro', 'Central Metro District', true),
    ('north-division', 'North Division', true),
    ('south-division', 'South Division', true),
    ('east-division', 'East Division', true),
    ('west-division', 'West Division', true),
    ('harbor-zone', 'Harbor & Transit Zone', true)
ON CONFLICT (id) DO NOTHING;

-- 3. Police officer verification profiles (owner-readable, never directly user-writable)
CREATE TABLE IF NOT EXISTS public.profiles (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    designation TEXT NOT NULL,
    police_station TEXT NOT NULL,
    claimed_district_id TEXT NOT NULL REFERENCES public.districts(id),
    approved_district_id TEXT REFERENCES public.districts(id),
    employee_ref TEXT,
    verification_status TEXT NOT NULL DEFAULT 'draft'
        CHECK (verification_status IN (
            'draft',
            'pending_review',
            'additional_info_required',
            'approved',
            'rejected',
            'suspended'
        )),
    status_reason TEXT,
    submission_version INTEGER NOT NULL DEFAULT 1,
    submitted_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS profiles_district_status_idx
    ON public.profiles(claimed_district_id, verification_status);
CREATE INDEX IF NOT EXISTS profiles_status_idx
    ON public.profiles(verification_status);

-- 4. District administrators (provisioned strictly server-side)
CREATE TABLE IF NOT EXISTS public.district_admins (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    district_id TEXT NOT NULL REFERENCES public.districts(id),
    is_active BOOLEAN NOT NULL DEFAULT true,
    provisioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS district_admins_district_active_idx
    ON public.district_admins(district_id, is_active);

-- 5. Supporting verification documents (private storage metadata only)
CREATE TABLE IF NOT EXISTS public.verification_documents (
    id UUID PRIMARY KEY,
    applicant_user_id UUID NOT NULL REFERENCES public.profiles(user_id) ON DELETE CASCADE,
    document_type TEXT NOT NULL
        CHECK (document_type IN ('redacted_id', 'authorization_letter', 'posting_confirmation')),
    storage_key TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL
        CHECK (mime_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    byte_size INTEGER NOT NULL CHECK (byte_size > 0 AND byte_size <= 5242880),
    sha256 TEXT NOT NULL,
    submission_version INTEGER NOT NULL DEFAULT 1,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS verification_documents_applicant_ver_idx
    ON public.verification_documents(applicant_user_id, submission_version);

-- 6. Append-only administrative verification review history
CREATE TABLE IF NOT EXISTS public.verification_reviews (
    id UUID PRIMARY KEY,
    applicant_user_id UUID NOT NULL REFERENCES public.profiles(user_id) ON DELETE RESTRICT,
    admin_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    district_id TEXT NOT NULL REFERENCES public.districts(id),
    decision TEXT NOT NULL
        CHECK (decision IN ('approved', 'rejected', 'additional_info_required', 'suspended', 'reinstated')),
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reason TEXT,
    request_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (applicant_user_id, request_key)
);

CREATE INDEX IF NOT EXISTS verification_reviews_district_created_idx
    ON public.verification_reviews(district_id, created_at DESC);
CREATE INDEX IF NOT EXISTS verification_reviews_applicant_created_idx
    ON public.verification_reviews(applicant_user_id, created_at DESC);

-- 7. Sanitized Realtime notification table for approval/status updates
CREATE TABLE IF NOT EXISTS public.verification_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    district_id TEXT NOT NULL REFERENCES public.districts(id),
    kind TEXT NOT NULL CHECK (kind IN ('application_submitted', 'status_changed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS verification_events_user_id_idx
    ON public.verification_events(user_id, id);
CREATE INDEX IF NOT EXISTS verification_events_district_id_idx
    ON public.verification_events(district_id, id);

-- 8. Security-definer helper predicates (prevent direct table grants on district_admins)
CREATE OR REPLACE FUNCTION public.is_approved_officer(target_uid UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.profiles p
        WHERE p.user_id = target_uid
          AND p.user_id = auth.uid()
          AND p.verification_status = 'approved'
    );
$$;

CREATE OR REPLACE FUNCTION public.is_active_district_admin(target_district TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.district_admins da
        WHERE da.user_id = auth.uid()
          AND da.is_active = true
          AND da.district_id = target_district
    );
$$;

REVOKE ALL ON FUNCTION public.is_approved_officer(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.is_active_district_admin(TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.is_approved_officer(UUID) FROM anon;
        REVOKE ALL ON FUNCTION public.is_active_district_admin(TEXT) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.is_approved_officer(UUID) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.is_active_district_admin(TEXT) TO authenticated;
    END IF;
END $$;

-- 9. Enable RLS on all new tables and revoke direct privileges
ALTER TABLE public.districts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.district_admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.districts, public.profiles, public.district_admins,
    public.verification_documents, public.verification_reviews, public.verification_events
    FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE public.districts, public.profiles, public.district_admins,
            public.verification_documents, public.verification_reviews, public.verification_events
            FROM anon;
        GRANT SELECT (id, name, is_active) ON public.districts TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE public.districts, public.profiles, public.district_admins,
            public.verification_documents, public.verification_reviews, public.verification_events
            FROM authenticated;
        GRANT SELECT (id, name, is_active) ON public.districts TO authenticated;
        GRANT SELECT (
            user_id, full_name, designation, police_station,
            claimed_district_id, approved_district_id, verification_status,
            status_reason, submission_version, submitted_at, approved_at, updated_at
        ) ON public.profiles TO authenticated;
        GRANT SELECT (id, user_id, district_id, kind, created_at)
            ON public.verification_events TO authenticated;
    END IF;
END $$;

-- 10. Policies for verification tables
DROP POLICY IF EXISTS districts_public_select ON public.districts;
CREATE POLICY districts_public_select ON public.districts
    FOR SELECT TO anon, authenticated
    USING (is_active = true);

DROP POLICY IF EXISTS profiles_self_or_district_admin_select ON public.profiles;
CREATE POLICY profiles_self_or_district_admin_select ON public.profiles
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.is_active_district_admin(claimed_district_id));

DROP POLICY IF EXISTS verification_events_scoped_select ON public.verification_events;
CREATE POLICY verification_events_scoped_select ON public.verification_events
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.is_active_district_admin(district_id));

-- 11. Tighten investigation RLS policies: require BOTH ownership AND approved officer status
DROP POLICY IF EXISTS cases_owner_select ON public.cases;
CREATE POLICY cases_owner_select ON public.cases
    FOR SELECT TO authenticated
    USING (owner_id IS NOT NULL AND owner_id = auth.uid() AND public.is_approved_officer(auth.uid()));

DROP POLICY IF EXISTS videos_owner_select ON public.videos;
CREATE POLICY videos_owner_select ON public.videos
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = videos.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS recordings_owner_select ON public.recordings;
CREATE POLICY recordings_owner_select ON public.recordings
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = recordings.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS investigation_states_owner_select ON public.investigation_states;
CREATE POLICY investigation_states_owner_select ON public.investigation_states
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = investigation_states.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS investigation_jobs_owner_select ON public.investigation_jobs;
CREATE POLICY investigation_jobs_owner_select ON public.investigation_jobs
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = investigation_jobs.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS sightings_owner_select ON public.sightings;
CREATE POLICY sightings_owner_select ON public.sightings
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = sightings.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS finding_reviews_owner_select ON public.finding_reviews;
CREATE POLICY finding_reviews_owner_select ON public.finding_reviews
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = finding_reviews.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS case_events_owner_select ON public.case_events;
CREATE POLICY case_events_owner_select ON public.case_events
    FOR SELECT TO authenticated
    USING (
        public.is_approved_officer(auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.cases c
            WHERE c.case_id = case_events.case_id AND c.owner_id IS NOT NULL AND c.owner_id = auth.uid()
        )
    );

-- 12. Add verification_events to Supabase Realtime publication
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'verification_events'
        ) THEN
            ALTER PUBLICATION supabase_realtime ADD TABLE public.verification_events;
        END IF;
    END IF;
END $$;

INSERT INTO public.schema_migrations(version, applied_at)
VALUES (5, CURRENT_TIMESTAMP::text)
ON CONFLICT(version) DO NOTHING;
