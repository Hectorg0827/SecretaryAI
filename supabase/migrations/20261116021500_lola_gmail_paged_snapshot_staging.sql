-- Lola Stage 13: bounded, resumable Gmail metadata snapshot ledger.
-- STAGING CANDIDATE ONLY. Do not apply to hosted Supabase without separate
-- project-specific approval. Additive, empty by default, no scheduled worker.
-- A staged snapshot is NOT an authorized provider cursor checkpoint and
-- NEVER grants permission to conclude a customer did not respond.
BEGIN;

CREATE TABLE public.lola_gmail_snapshot_jobs (
  company_id UUID NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL DEFAULT 'gmail' CHECK (provider='gmail'),
  provider_identity_sha256 TEXT NOT NULL
    CHECK (provider_identity_sha256 ~ '^[0-9a-f]{64}$'),
  resource_sha256 TEXT NOT NULL CHECK (resource_sha256 ~ '^[0-9a-f]{64}$'),
  mapping_revision TEXT NOT NULL CHECK (length(btrim(mapping_revision)) BETWEEN 1 AND 200),
  initial_history_ciphertext TEXT NOT NULL
    CHECK (length(initial_history_ciphertext) BETWEEN 40 AND 8192),
  initial_history_sha256 TEXT NOT NULL
    CHECK (initial_history_sha256 ~ '^[0-9a-f]{64}$'),
  next_page_ciphertext TEXT,
  next_page_sha256 TEXT,
  page_chain_sha256 TEXT NOT NULL CHECK (page_chain_sha256 ~ '^[0-9a-f]{64}$'),
  pages_staged INTEGER NOT NULL DEFAULT 0 CHECK (pages_staged BETWEEN 0 AND 500),
  messages_staged INTEGER NOT NULL DEFAULT 0 CHECK (messages_staged BETWEEN 0 AND 20000),
  state TEXT NOT NULL DEFAULT 'collecting'
    CHECK (state IN ('collecting','snapshot_exhausted','aborted')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (company_id,employee_id,provider,provider_identity_sha256,resource_sha256),
  CHECK (
    (state='collecting' AND next_page_ciphertext IS NOT NULL AND next_page_sha256 IS NOT NULL)
    OR (state='snapshot_exhausted' AND next_page_ciphertext IS NULL AND next_page_sha256 IS NULL)
    OR state='aborted'
  ),
  CHECK (next_page_ciphertext IS NULL OR length(next_page_ciphertext) BETWEEN 40 AND 8192),
  CHECK (next_page_sha256 IS NULL OR next_page_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX lola_gmail_snapshot_jobs_employee
  ON public.lola_gmail_snapshot_jobs(company_id,employee_id,state,updated_at DESC);

CREATE TABLE public.lola_gmail_snapshot_pages (
  company_id UUID NOT NULL,
  employee_id UUID NOT NULL,
  provider TEXT NOT NULL CHECK (provider='gmail'),
  provider_identity_sha256 TEXT NOT NULL,
  resource_sha256 TEXT NOT NULL,
  page_index INTEGER NOT NULL CHECK (page_index BETWEEN 0 AND 499),
  page_digest_sha256 TEXT NOT NULL CHECK (page_digest_sha256 ~ '^[0-9a-f]{64}$'),
  entry_count INTEGER NOT NULL CHECK (entry_count BETWEEN 0 AND 100),
  chain_digest_sha256 TEXT NOT NULL CHECK (chain_digest_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,page_index
  ),
  FOREIGN KEY (company_id,employee_id,provider,provider_identity_sha256,resource_sha256)
    REFERENCES public.lola_gmail_snapshot_jobs(
      company_id,employee_id,provider,provider_identity_sha256,resource_sha256
    ) ON DELETE CASCADE
);

CREATE TABLE public.lola_gmail_snapshot_refs (
  company_id UUID NOT NULL,
  employee_id UUID NOT NULL,
  provider TEXT NOT NULL CHECK (provider='gmail'),
  provider_identity_sha256 TEXT NOT NULL,
  resource_sha256 TEXT NOT NULL,
  message_key_sha256 TEXT NOT NULL CHECK (message_key_sha256 ~ '^[0-9a-f]{64}$'),
  thread_key_sha256 TEXT CHECK (thread_key_sha256 ~ '^[0-9a-f]{64}$'),
  metadata_history_key_sha256 TEXT NOT NULL
    CHECK (metadata_history_key_sha256 ~ '^[0-9a-f]{64}$'),
  page_index INTEGER NOT NULL CHECK (page_index BETWEEN 0 AND 499),
  PRIMARY KEY (
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
    message_key_sha256
  ),
  FOREIGN KEY (
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,page_index
  ) REFERENCES public.lola_gmail_snapshot_pages(
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,page_index
  ) ON DELETE CASCADE
);
CREATE INDEX lola_gmail_snapshot_refs_page
  ON public.lola_gmail_snapshot_refs(company_id,employee_id,page_index);

ALTER TABLE public.lola_gmail_snapshot_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lola_gmail_snapshot_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lola_gmail_snapshot_refs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lola_gmail_snapshot_jobs
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON public.lola_gmail_snapshot_pages
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON public.lola_gmail_snapshot_refs
  FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.lola_gmail_snapshot_jobs TO service_role;
GRANT SELECT,INSERT ON public.lola_gmail_snapshot_pages TO service_role;
GRANT SELECT,INSERT ON public.lola_gmail_snapshot_refs TO service_role;

-- Stage 10's verified employee/consent/resource/work-item mapping guard runs
-- on every job create/update. Revocations lock against the SQL transaction.
CREATE TRIGGER lola_gmail_snapshot_consent_gate
  BEFORE INSERT OR UPDATE ON public.lola_gmail_snapshot_jobs
  FOR EACH ROW EXECUTE FUNCTION public.lola_guard_provider_replay_checkpoint();

CREATE FUNCTION public.lola_stage_gmail_snapshot_page(
  p_company_id UUID,
  p_employee_id UUID,
  p_provider_identity_sha256 TEXT,
  p_resource_sha256 TEXT,
  p_mapping_revision TEXT,
  p_expected_pages INTEGER,
  p_expected_page_cursor_sha256 TEXT,
  p_initial_history_ciphertext TEXT,
  p_initial_history_sha256 TEXT,
  p_next_page_ciphertext TEXT,
  p_next_page_sha256 TEXT,
  p_page_digest_sha256 TEXT,
  p_entries JSONB,
  p_exhausted BOOLEAN
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=pg_catalog,public
AS $body$
DECLARE
  v_job public.lola_gmail_snapshot_jobs%ROWTYPE;
  v_page INTEGER;
  v_count INTEGER;
  v_chain TEXT;
  v_entry JSONB;
  v_message TEXT;
  v_thread TEXT;
  v_history TEXT;
  v_seen TEXT[] := ARRAY[]::TEXT[];
BEGIN
  IF current_user IS DISTINCT FROM 'service_role'
     OR current_setting('request.jwt.claim.role',TRUE) IS DISTINCT FROM 'service_role' THEN
    RAISE EXCEPTION 'Gmail snapshot writer unavailable';
  END IF;
  IF p_company_id IS NULL OR p_employee_id IS NULL
     OR p_provider_identity_sha256 !~ '^[0-9a-f]{64}$'
     OR p_resource_sha256 !~ '^[0-9a-f]{64}$'
     OR length(btrim(p_mapping_revision)) NOT BETWEEN 1 AND 200
     OR p_expected_pages IS NULL OR p_expected_pages NOT BETWEEN 0 AND 499
     OR (p_expected_page_cursor_sha256 IS NOT NULL
          AND p_expected_page_cursor_sha256 !~ '^[0-9a-f]{64}$')
     OR p_initial_history_ciphertext !~ '^v1:[A-Za-z0-9+/=]+$'
     OR length(p_initial_history_ciphertext) NOT BETWEEN 40 AND 8192
     OR p_initial_history_sha256 !~ '^[0-9a-f]{64}$'
     OR (p_next_page_ciphertext IS NOT NULL
          AND (p_next_page_ciphertext !~ '^v1:[A-Za-z0-9+/=]+$'
               OR length(p_next_page_ciphertext) NOT BETWEEN 40 AND 8192))
     OR (p_next_page_sha256 IS NOT NULL
          AND p_next_page_sha256 !~ '^[0-9a-f]{64}$')
     OR p_page_digest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_entries IS NULL OR jsonb_typeof(p_entries) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_entries) > 100
     OR p_exhausted IS NULL
     OR (p_exhausted AND
         (p_next_page_ciphertext IS NOT NULL OR p_next_page_sha256 IS NOT NULL))
     OR (NOT p_exhausted AND
         (p_next_page_ciphertext IS NULL OR p_next_page_sha256 IS NULL))
  THEN
    RAISE EXCEPTION 'Gmail snapshot page evidence malformed';
  END IF;

  v_count:=jsonb_array_length(p_entries);
  IF v_count=0 AND NOT p_exhausted THEN
    RAISE EXCEPTION 'Gmail snapshot cannot advance on empty page';
  END IF;

  SELECT * INTO v_job FROM public.lola_gmail_snapshot_jobs
  WHERE company_id=p_company_id AND employee_id=p_employee_id
    AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256
  FOR UPDATE;

  IF NOT FOUND THEN
    IF p_expected_pages<>0 OR p_expected_page_cursor_sha256 IS NOT NULL THEN
      RAISE EXCEPTION 'Gmail snapshot initial page required';
    END IF;
    v_chain:=encode(sha256(convert_to(concat_ws(':',
      'lola-gmail-snapshot-chain-v1',p_initial_history_sha256,p_page_digest_sha256
    ),'UTF8')),'hex');
    INSERT INTO public.lola_gmail_snapshot_jobs(
      company_id,employee_id,provider,provider_identity_sha256,
      resource_sha256,mapping_revision,initial_history_ciphertext,
      initial_history_sha256,next_page_ciphertext,next_page_sha256,
      page_chain_sha256,pages_staged,messages_staged,state
    ) VALUES (
      p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,
      p_resource_sha256,p_mapping_revision,p_initial_history_ciphertext,
      p_initial_history_sha256,p_next_page_ciphertext,p_next_page_sha256,
      v_chain,1,v_count,
      CASE WHEN p_exhausted THEN 'snapshot_exhausted' ELSE 'collecting' END
    );
    v_page:=0;
  ELSE
    IF v_job.state IS DISTINCT FROM 'collecting'
       OR v_job.pages_staged IS DISTINCT FROM p_expected_pages
       OR v_job.next_page_sha256 IS DISTINCT FROM p_expected_page_cursor_sha256
       OR v_job.mapping_revision IS DISTINCT FROM p_mapping_revision
       OR v_job.initial_history_sha256 IS DISTINCT FROM p_initial_history_sha256
       OR v_job.messages_staged+v_count>20000
       OR v_job.pages_staged>=500
    THEN
      RAISE EXCEPTION 'Gmail snapshot resume compare-and-swap conflict';
    END IF;
    v_page:=v_job.pages_staged;
    v_chain:=encode(sha256(convert_to(concat_ws(':',
      v_job.page_chain_sha256,p_page_digest_sha256
    ),'UTF8')),'hex');
    UPDATE public.lola_gmail_snapshot_jobs
    SET next_page_ciphertext=p_next_page_ciphertext,
        next_page_sha256=p_next_page_sha256,
        page_chain_sha256=v_chain,
        pages_staged=pages_staged+1,
        messages_staged=messages_staged+v_count,
        state=CASE WHEN p_exhausted THEN 'snapshot_exhausted' ELSE 'collecting' END,
        updated_at=now()
    WHERE company_id=p_company_id AND employee_id=p_employee_id
      AND provider='gmail'
      AND provider_identity_sha256=p_provider_identity_sha256
      AND resource_sha256=p_resource_sha256;
  END IF;

  INSERT INTO public.lola_gmail_snapshot_pages(
    company_id,employee_id,provider,provider_identity_sha256,
    resource_sha256,page_index,page_digest_sha256,entry_count,chain_digest_sha256
  ) VALUES (
    p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,
    p_resource_sha256,v_page,p_page_digest_sha256,v_count,v_chain
  );

  FOR v_entry IN SELECT value FROM jsonb_array_elements(p_entries)
  LOOP
    v_message:=v_entry->>'message_key_sha256';
    v_thread:=v_entry->>'thread_key_sha256';
    v_history:=v_entry->>'metadata_history_key_sha256';
    IF v_message !~ '^[0-9a-f]{64}$'
       OR (v_thread IS NOT NULL AND v_thread !~ '^[0-9a-f]{64}$')
       OR v_history !~ '^[0-9a-f]{64}$'
       OR v_message=ANY(v_seen)
    THEN RAISE EXCEPTION 'Gmail snapshot message reference invalid'; END IF;
    v_seen:=array_append(v_seen,v_message);
    INSERT INTO public.lola_gmail_snapshot_refs(
      company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
      message_key_sha256,thread_key_sha256,metadata_history_key_sha256,page_index
    ) VALUES (
      p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,
      p_resource_sha256,v_message,v_thread,v_history,v_page
    );
    -- A duplicate across previously committed pages raises 23505 and
    -- atomically rolls back this page, its cursor and receipt.
  END LOOP;
  RETURN v_page+1;
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_stage_gmail_snapshot_page(
  UUID,UUID,TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,BOOLEAN
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_stage_gmail_snapshot_page(
  UUID,UUID,TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,BOOLEAN
) TO service_role;
COMMIT;
