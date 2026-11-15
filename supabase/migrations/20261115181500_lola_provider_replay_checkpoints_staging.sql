-- Lola Stage 8: staging-only, additive backend-owned provider synchronization.
-- DO NOT APPLY TO HOSTED SUPABASE WITHOUT EXPLICIT PROJECT-SPECIFIC APPROVAL.
-- Provider cursors, including Microsoft Graph deltaLinks, can act as sensitive
-- access capability; the backend MUST encrypt them before calling this RPC.
-- Neither a committed cursor nor metadata change record proves a reply absence.
-- The existing verified identity/employee consent registry is a separate gate.
BEGIN;

CREATE TABLE IF NOT EXISTS public.lola_provider_replay_checkpoints (
    company_id UUID NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('gmail','outlook')),
    provider_identity_sha256 TEXT NOT NULL
        CHECK (provider_identity_sha256 ~ '^[0-9a-f]{64}$'),
    resource_sha256 TEXT NOT NULL
        CHECK (resource_sha256 ~ '^[0-9a-f]{64}$'),
    mapping_revision TEXT NOT NULL CHECK (length(btrim(mapping_revision)) BETWEEN 1 AND 200),
    -- Application-encrypted, never plain Gmail history ID or Graph deltaLink.
    cursor_ciphertext TEXT NOT NULL CHECK (length(cursor_ciphertext) BETWEEN 40 AND 8192),
    cursor_sha256 TEXT NOT NULL CHECK (cursor_sha256 ~ '^[0-9a-f]{64}$'),
    replay_digest TEXT NOT NULL CHECK (replay_digest ~ '^[0-9a-f]{64}$'),
    baseline_only BOOLEAN NOT NULL DEFAULT TRUE,
    replayed_through TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    generation BIGINT NOT NULL DEFAULT 1 CHECK (generation >= 1),
    PRIMARY KEY (
        company_id,employee_id,provider,provider_identity_sha256,resource_sha256
    ),
    CHECK (replayed_through <= committed_at + INTERVAL '5 minutes')
);
CREATE INDEX IF NOT EXISTS lola_provider_replay_checkpoint_scope_idx
    ON public.lola_provider_replay_checkpoints
    (company_id,employee_id,provider,committed_at DESC);

CREATE TABLE IF NOT EXISTS public.lola_provider_replay_events (
    company_id UUID NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('gmail','outlook')),
    provider_identity_sha256 TEXT NOT NULL
        CHECK (provider_identity_sha256 ~ '^[0-9a-f]{64}$'),
    resource_sha256 TEXT NOT NULL CHECK (resource_sha256 ~ '^[0-9a-f]{64}$'),
    event_key_sha256 TEXT NOT NULL CHECK (event_key_sha256 ~ '^[0-9a-f]{64}$'),
    operation TEXT NOT NULL CHECK (
        operation IN ('added','deleted','label_changed','changed')
    ),
    replay_digest TEXT NOT NULL CHECK (replay_digest ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (
        company_id,employee_id,provider,provider_identity_sha256,
        resource_sha256,event_key_sha256
    )
);
CREATE INDEX IF NOT EXISTS lola_provider_replay_events_scope_idx
    ON public.lola_provider_replay_events
    (company_id,employee_id,provider,recorded_at DESC);

ALTER TABLE public.lola_provider_replay_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lola_provider_replay_events ENABLE ROW LEVEL SECURITY;
-- No end-user SELECT / INSERT / UPDATE / DELETE policies.
REVOKE ALL ON public.lola_provider_replay_checkpoints
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.lola_provider_replay_events
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT,INSERT,UPDATE ON public.lola_provider_replay_checkpoints TO service_role;
GRANT SELECT,INSERT ON public.lola_provider_replay_events TO service_role;

-- Atomic, compare-and-swap checkpoint + event batch. SECURITY INVOKER means
-- the executing role must possess the explicitly granted table privileges.
CREATE OR REPLACE FUNCTION public.lola_commit_provider_replay(
    p_company_id UUID,
    p_employee_id UUID,
    p_provider TEXT,
    p_provider_identity_sha256 TEXT,
    p_resource_sha256 TEXT,
    p_mapping_revision TEXT,
    p_expected_generation BIGINT,
    p_cursor_ciphertext TEXT,
    p_cursor_sha256 TEXT,
    p_replay_digest TEXT,
    p_baseline_only BOOLEAN,
    p_replayed_through TIMESTAMPTZ,
    p_events JSONB
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $body$
DECLARE
    v_current_generation BIGINT;
    v_next_generation BIGINT;
    v_event JSONB;
    v_seen TEXT[] := ARRAY[]::TEXT[];
    v_key TEXT;
    v_kind TEXT;
BEGIN
    -- Backend service role only; never expose cursor submission to browsers.
    IF current_setting('request.jwt.claim.role', TRUE) IS DISTINCT FROM 'service_role'
       AND auth.role() IS DISTINCT FROM 'service_role' THEN
        RAISE EXCEPTION 'Provider replay writer unavailable';
    END IF;

    IF p_company_id IS NULL OR p_employee_id IS NULL
       OR p_provider NOT IN ('gmail','outlook')
       OR p_provider_identity_sha256 !~ '^[0-9a-f]{64}$'
       OR p_resource_sha256 !~ '^[0-9a-f]{64}$'
       OR length(btrim(p_mapping_revision)) NOT BETWEEN 1 AND 200
       OR p_expected_generation IS NULL OR p_expected_generation < 0
       OR p_cursor_ciphertext IS NULL
       OR length(p_cursor_ciphertext) NOT BETWEEN 40 AND 8192
       OR p_cursor_sha256 !~ '^[0-9a-f]{64}$'
       OR p_replay_digest !~ '^[0-9a-f]{64}$'
       OR p_baseline_only IS NULL
       OR p_replayed_through IS NULL
       OR p_replayed_through > now() + INTERVAL '5 minutes'
       OR p_events IS NULL OR jsonb_typeof(p_events) <> 'array'
       OR jsonb_array_length(p_events) > 500 THEN
        RAISE EXCEPTION 'Provider replay evidence malformed';
    END IF;

    SELECT generation INTO v_current_generation
    FROM public.lola_provider_replay_checkpoints
    WHERE company_id = p_company_id AND employee_id = p_employee_id
      AND provider = p_provider
      AND provider_identity_sha256 = p_provider_identity_sha256
      AND resource_sha256 = p_resource_sha256
    FOR UPDATE;

    IF NOT FOUND THEN
        IF p_expected_generation <> 0 OR p_baseline_only IS NOT TRUE THEN
            RAISE EXCEPTION 'Provider replay bootstrap required';
        END IF;
        INSERT INTO public.lola_provider_replay_checkpoints (
            company_id,employee_id,provider,provider_identity_sha256,
            resource_sha256,mapping_revision,cursor_ciphertext,cursor_sha256,
            replay_digest,baseline_only,replayed_through,generation
        ) VALUES (
            p_company_id,p_employee_id,p_provider,p_provider_identity_sha256,
            p_resource_sha256,p_mapping_revision,p_cursor_ciphertext,p_cursor_sha256,
            p_replay_digest,p_baseline_only,p_replayed_through,1
        );
        v_next_generation := 1;
    ELSE
        IF v_current_generation <> p_expected_generation THEN
            RAISE EXCEPTION 'Provider replay generation conflict';
        END IF;
        -- An identity revision change invalidates the prior cursor. The
        -- employee must re-bootstrap rather than attaching old evidence to
        -- a newly mapped customer, mailbox, folder or consent context.
        IF NOT EXISTS (
            SELECT 1 FROM public.lola_provider_replay_checkpoints
            WHERE company_id = p_company_id AND employee_id = p_employee_id
              AND provider = p_provider
              AND provider_identity_sha256 = p_provider_identity_sha256
              AND resource_sha256 = p_resource_sha256
              AND mapping_revision = p_mapping_revision
        ) THEN
            RAISE EXCEPTION 'Provider mapping revision changed';
        END IF;
        UPDATE public.lola_provider_replay_checkpoints
        SET cursor_ciphertext = p_cursor_ciphertext,
            cursor_sha256 = p_cursor_sha256,
            replay_digest = p_replay_digest,
            baseline_only = (baseline_only OR p_baseline_only),
            replayed_through = p_replayed_through,
            committed_at = now(),
            generation = generation + 1
        WHERE company_id = p_company_id AND employee_id = p_employee_id
          AND provider = p_provider
          AND provider_identity_sha256 = p_provider_identity_sha256
          AND resource_sha256 = p_resource_sha256;
        v_next_generation := v_current_generation + 1;
    END IF;

    FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
    LOOP
        v_key := v_event->>'key_sha256';
        v_kind := v_event->>'operation';
        IF v_key IS NULL OR v_key !~ '^[0-9a-f]{64}$'
           OR v_kind NOT IN ('added','deleted','label_changed','changed')
           OR v_key = ANY(v_seen) THEN
            RAISE EXCEPTION 'Provider replay event batch malformed';
        END IF;
        v_seen := array_append(v_seen, v_key);
        INSERT INTO public.lola_provider_replay_events (
            company_id,employee_id,provider,provider_identity_sha256,
            resource_sha256,event_key_sha256,operation,replay_digest
        ) VALUES (
            p_company_id,p_employee_id,p_provider,p_provider_identity_sha256,
            p_resource_sha256,v_key,v_kind,p_replay_digest
        ) ON CONFLICT DO NOTHING;
    END LOOP;
    RETURN v_next_generation;
END;
$body$;

REVOKE ALL ON FUNCTION public.lola_commit_provider_replay(
    UUID,UUID,TEXT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,BOOLEAN,TIMESTAMPTZ,JSONB
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_commit_provider_replay(
    UUID,UUID,TEXT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,BOOLEAN,TIMESTAMPTZ,JSONB
) TO service_role;
COMMIT;
