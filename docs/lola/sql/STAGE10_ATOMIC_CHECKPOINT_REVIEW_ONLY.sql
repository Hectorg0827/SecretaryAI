-- STAGE 10 REVIEW DRAFT ONLY. NOT A GENERATED SUPABASE MIGRATION.
-- Do NOT apply to any hosted Supabase project, staging or production.
-- Once Supabase CLI is available, generate ONE new migration with
-- "supabase migration new <name>" and transfer a reviewed/tested version.
--
-- This draft is a security/transaction implementation candidate. It has
-- NOT been executed against PostgreSQL, so it MUST NOT be treated as
-- deployment-ready or a proven transactional implementation.
--
-- Intended environment: SecretaryAI-Staging, PG15+, pgcrypto installed in
-- extensions schema; existing public.users, public.employee_integrations,
-- public.lola_private_contacts and public.lola_employee_whatsapp_connections.
BEGIN;

-- Current provider consent and independently verified contact/case linkage.
-- No public/authed/browser grants. Tables are intentionally empty after
-- creation: no bootstrap identities or provider consents are fabricated.
CREATE TABLE public.lola_channel_authorizations (
    company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
    employee_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('email','whatsapp_business')),
    provider_identity_ref text NOT NULL CHECK (length(provider_identity_ref) BETWEEN 1 AND 200),
    provider_verification_state text NOT NULL DEFAULT 'pending'
        CHECK (provider_verification_state IN ('pending','verified','revoked')),
    connector_authorization_state text NOT NULL DEFAULT 'pending'
        CHECK (connector_authorization_state IN ('pending','connected','disconnected','revoked')),
    employee_consent_at timestamptz,
    employee_consent_revoked_at timestamptz,
    provider_verified_at timestamptz,
    authorization_expires_at timestamptz,
    source_revision text NOT NULL CHECK (length(source_revision) BETWEEN 1 AND 200),
    PRIMARY KEY(company_id,employee_id,channel),
    FOREIGN KEY(company_id,employee_id)
       REFERENCES public.users(company_id,id) ON DELETE CASCADE
);

CREATE TABLE public.lola_verified_case_links (
    company_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    work_item_id text NOT NULL CHECK (length(work_item_id) BETWEEN 1 AND 200),
    email_provider_contact_ref text NOT NULL
        CHECK (length(email_provider_contact_ref) BETWEEN 1 AND 200),
    whatsapp_provider_contact_ref text NOT NULL
        CHECK (length(whatsapp_provider_contact_ref) BETWEEN 1 AND 200),
    link_verification_state text NOT NULL DEFAULT 'pending'
        CHECK (link_verification_state IN ('pending','verified','revoked')),
    verification_source text NOT NULL DEFAULT 'pending'
        CHECK (verification_source IN ('pending','provider_plus_employee')),
    employee_confirmed_at timestamptz,
    link_verified_at timestamptz,
    link_revoked_at timestamptz,
    verification_expires_at timestamptz,
    mapping_revision text NOT NULL CHECK (length(mapping_revision) BETWEEN 1 AND 200),
    PRIMARY KEY(company_id,employee_id,contact_id,work_item_id),
    FOREIGN KEY(company_id,employee_id,contact_id)
       REFERENCES public.lola_private_contacts(company_id,user_id,id) ON DELETE CASCADE
);

CREATE TABLE public.lola_delta_checkpoints (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    provider text NOT NULL CHECK (provider IN ('gmail','outlook')),
    provider_connection_ref text NOT NULL CHECK (length(provider_connection_ref) BETWEEN 1 AND 256),
    provider_identity_ref text NOT NULL CHECK (length(provider_identity_ref) BETWEEN 1 AND 256),
    mailbox_ref text NOT NULL CHECK (length(mailbox_ref) BETWEEN 1 AND 256),
    -- Non-null folder_key prevents duplicated Gmail rows through NULL semantics.
    folder_key text NOT NULL DEFAULT '',
    mapping_revision text NOT NULL CHECK (mapping_revision ~ '^[0-9a-f]{64}$'),
    version bigint NOT NULL CHECK (version >= 1),
    cursor_ciphertext text NOT NULL CHECK (length(cursor_ciphertext) BETWEEN 32 AND 32768),
    cursor_digest text NOT NULL CHECK (cursor_digest ~ '^[0-9a-f]{64}$'),
    last_round_digest text NOT NULL CHECK (last_round_digest ~ '^[0-9a-f]{64}$'),
    changes_applied integer NOT NULL CHECK (changes_applied BETWEEN 0 AND 5000),
    committed_at timestamptz NOT NULL,
    source_coverage_start timestamptz,
    CHECK (
      (provider='gmail' AND folder_key='')
      OR (provider='outlook' AND length(folder_key) BETWEEN 1 AND 256)
    ),
    FOREIGN KEY(company_id,employee_id)
       REFERENCES public.users(company_id,id) ON DELETE CASCADE,
    CONSTRAINT lola_delta_checkpoint_exact_scope UNIQUE(
      company_id,employee_id,provider,provider_connection_ref,
      provider_identity_ref,mailbox_ref,folder_key,mapping_revision
    )
);
CREATE INDEX lola_delta_checkpoints_employee_idx
  ON public.lola_delta_checkpoints(company_id,employee_id,provider,committed_at DESC);

CREATE TABLE public.lola_delta_event_refs (
    checkpoint_id uuid NOT NULL
       REFERENCES public.lola_delta_checkpoints(id) ON DELETE CASCADE,
    event_ref text NOT NULL CHECK (event_ref ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL CHECK (
      kind IN ('added','deleted','label_added','label_removed','updated_or_added')
    ),
    thread_ref text CHECK (
      thread_ref IS NULL OR thread_ref ~ '^[0-9a-f]{64}$'
    ),
    committed_version bigint NOT NULL CHECK (committed_version > 0),
    inserted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(checkpoint_id,event_ref)
);
CREATE INDEX lola_delta_event_refs_version_idx
  ON public.lola_delta_event_refs(checkpoint_id,committed_version);

-- No browser policies, not even authenticated read. Existing service-role
-- server calls still check exact scope explicitly.
ALTER TABLE public.lola_channel_authorizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lola_verified_case_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lola_delta_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lola_delta_event_refs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lola_channel_authorizations,
  public.lola_verified_case_links,public.lola_delta_checkpoints,
  public.lola_delta_event_refs FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.lola_channel_authorizations,
  public.lola_verified_case_links TO service_role;
GRANT SELECT,INSERT,UPDATE ON public.lola_delta_checkpoints TO service_role;
-- Event refs are append-only at the application privilege layer.
-- No UPDATE or DELETE grant to the service_role, authenticated or anon.
GRANT SELECT,INSERT ON public.lola_delta_event_refs TO service_role;

-- This helper LOCKS current trusted membership, email consent, independent
-- case identity, and the existing WhatsApp Business Meta ownership record.
-- Competing revocation UPDATEs serialize with an in-flight commit.
-- Verifies Stage 5 combined_revision calculation exactly:
-- SHA256("company\nemployee\ncontact\nwork_item\nlink_revision\n"
--        "email:email_grant_revision\nwhatsapp_business:wa_grant_revision")
CREATE FUNCTION public.lola_delta_scope_allowed(
  p_company_id uuid,p_employee_id uuid,p_provider text,
  p_provider_connection_ref text,p_provider_identity_ref text,
  p_mailbox_ref text,p_folder_id text,p_mapping_revision text
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER VOLATILE
SET search_path=pg_catalog,public,extensions,pg_temp AS $allowed$
DECLARE v_found boolean := false;
BEGIN
  IF p_company_id IS NULL OR p_employee_id IS NULL
     OR p_provider NOT IN ('gmail','outlook')
     OR p_provider_connection_ref IS NULL OR length(p_provider_connection_ref) NOT BETWEEN 1 AND 256
     OR p_provider_identity_ref IS NULL OR length(p_provider_identity_ref) NOT BETWEEN 1 AND 256
     OR p_mailbox_ref IS NULL OR length(p_mailbox_ref) NOT BETWEEN 1 AND 256
     OR p_mapping_revision IS NULL OR p_mapping_revision !~ '^[0-9a-f]{64}$'
     OR (p_provider='gmail' AND p_folder_id IS NOT NULL)
     OR (p_provider='outlook' AND (p_folder_id IS NULL OR length(p_folder_id) NOT BETWEEN 1 AND 256))
  THEN
    RETURN false;
  END IF;

  -- Hold SHARE locks for the rest of the caller's transaction; locks are
  -- intentionally acquired before the checkpoint row's FOR UPDATE lock.
  PERFORM 1
  FROM public.users AS u
  JOIN public.employee_integrations AS ei
    ON ei.company_id=u.company_id AND ei.user_id=u.id
   AND ei.id::text=p_provider_connection_ref
   AND ei.provider=p_provider AND ei.connected_at IS NOT NULL
  JOIN public.lola_channel_authorizations AS email_grant
    ON email_grant.company_id=u.company_id
   AND email_grant.employee_id=u.id AND email_grant.channel='email'
  JOIN public.lola_channel_authorizations AS wa_grant
    ON wa_grant.company_id=u.company_id
   AND wa_grant.employee_id=u.id AND wa_grant.channel='whatsapp_business'
  JOIN public.lola_employee_whatsapp_connections AS wa
    ON wa.company_id=u.company_id AND wa.user_id=u.id
   AND wa.phone_number_id=wa_grant.provider_identity_ref
  JOIN public.lola_verified_case_links AS link
    ON link.company_id=u.company_id AND link.employee_id=u.id
  WHERE u.company_id=p_company_id AND u.id=p_employee_id
    AND u.is_active IS TRUE
    AND u.role IN ('owner','manager','back_office','sales_rep')
    AND email_grant.provider_identity_ref=p_provider_identity_ref
    AND email_grant.provider_verification_state='verified'
    AND email_grant.connector_authorization_state='connected'
    AND email_grant.employee_consent_at IS NOT NULL
    AND email_grant.employee_consent_revoked_at IS NULL
    AND email_grant.provider_verified_at IS NOT NULL
    AND email_grant.authorization_expires_at > now()
    AND wa_grant.provider_verification_state='verified'
    AND wa_grant.connector_authorization_state='connected'
    AND wa_grant.employee_consent_at IS NOT NULL
    AND wa_grant.employee_consent_revoked_at IS NULL
    AND wa_grant.provider_verified_at IS NOT NULL
    AND wa_grant.authorization_expires_at > now()
    AND wa.status='active'
    AND wa.meta_verified_at IS NOT NULL
    AND wa.employee_consented_at IS NOT NULL
    AND wa.employee_revoked_at IS NULL
    AND wa.history_sync_consented IS TRUE
    AND wa.policy_clearance_id IS NOT NULL
    AND link.link_verification_state='verified'
    AND link.verification_source='provider_plus_employee'
    AND link.employee_confirmed_at IS NOT NULL
    AND link.link_verified_at IS NOT NULL
    AND link.link_revoked_at IS NULL
    AND link.verification_expires_at > now()
    AND encode(extensions.digest(
      convert_to(
        concat_ws(E'\n',
          p_company_id::text,p_employee_id::text,link.contact_id::text,
          link.work_item_id,link.mapping_revision,
          'email:' || email_grant.source_revision,
          'whatsapp_business:' || wa_grant.source_revision
        ),'UTF8'
      ),'sha256'),'hex')=p_mapping_revision
  FOR SHARE OF u,ei,email_grant,wa_grant,wa,link;
  v_found := FOUND;
  RETURN v_found;
END
$allowed$;

CREATE FUNCTION public.lola_delta_checkpoint_read(
  p_company_id uuid,p_employee_id uuid,p_provider text,
  p_provider_connection_ref text,p_provider_identity_ref text,
  p_mailbox_ref text,p_folder_id text,p_mapping_revision text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER VOLATILE
SET search_path=pg_catalog,public,extensions,pg_temp AS $read$
DECLARE checkpoint public.lola_delta_checkpoints%ROWTYPE;
BEGIN
  IF NOT public.lola_delta_scope_allowed(
    p_company_id,p_employee_id,p_provider,p_provider_connection_ref,
    p_provider_identity_ref,p_mailbox_ref,p_folder_id,p_mapping_revision
  ) THEN
    RAISE EXCEPTION 'Current employee/provider scope not verified'
      USING ERRCODE='42501';
  END IF;

  SELECT * INTO checkpoint FROM public.lola_delta_checkpoints AS c
  WHERE c.company_id=p_company_id AND c.employee_id=p_employee_id
    AND c.provider=p_provider
    AND c.provider_connection_ref=p_provider_connection_ref
    AND c.provider_identity_ref=p_provider_identity_ref
    AND c.mailbox_ref=p_mailbox_ref
    AND c.folder_key=coalesce(p_folder_id,'')
    AND c.mapping_revision=p_mapping_revision;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  -- Never return a raw provider cursor. Only its encrypted ciphertext
  -- reaches the service-role backend, which verifies HMAC after decrypt.
  RETURN jsonb_build_object(
    'company_id',checkpoint.company_id::text,
    'employee_id',checkpoint.employee_id::text,
    'provider',checkpoint.provider,
    'provider_connection_ref',checkpoint.provider_connection_ref,
    'provider_identity_ref',checkpoint.provider_identity_ref,
    'mailbox_ref',checkpoint.mailbox_ref,
    'folder_id',nullif(checkpoint.folder_key,''),
    'mapping_revision',checkpoint.mapping_revision,
    'cursor_ciphertext',checkpoint.cursor_ciphertext,
    'cursor_digest',checkpoint.cursor_digest,
    'version',checkpoint.version,
    'last_round_digest',checkpoint.last_round_digest,
    'changes_applied',checkpoint.changes_applied,
    'committed_at',checkpoint.committed_at,
    'source_coverage_start',checkpoint.source_coverage_start,
    'events_and_cursor_single_transaction',true,
    'current_employee_authorization_checked',true
  );
END
$read$;

CREATE FUNCTION public.lola_delta_checkpoint_commit(
  p_company_id uuid,p_employee_id uuid,p_provider text,
  p_provider_connection_ref text,p_provider_identity_ref text,
  p_mailbox_ref text,p_folder_id text,p_mapping_revision text,
  p_expected_version integer,p_expected_cursor_digest text,
  p_terminal_cursor_ciphertext text,p_terminal_cursor_digest text,
  p_last_round_digest text,p_changes jsonb,p_observed_at timestamptz
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER VOLATILE
SET search_path=pg_catalog,public,extensions,pg_temp AS $commit$
DECLARE checkpoint public.lola_delta_checkpoints%ROWTYPE;
        v_id uuid; v_version bigint; v_change jsonb;
        v_folder_key text := coalesce(p_folder_id,'');
        v_now timestamptz := clock_timestamp();
BEGIN
  IF p_expected_version IS NULL OR p_expected_version < 0
     OR (p_expected_version=0 AND (p_provider<>'outlook' OR p_expected_cursor_digest IS NOT NULL))
     OR (p_expected_version>0 AND (p_expected_cursor_digest IS NULL
            OR p_expected_cursor_digest !~ '^[0-9a-f]{64}$'))
     OR p_terminal_cursor_ciphertext IS NULL
     OR length(p_terminal_cursor_ciphertext) NOT BETWEEN 32 AND 32768
     OR p_terminal_cursor_digest IS NULL
     OR p_terminal_cursor_digest !~ '^[0-9a-f]{64}$'
     OR p_last_round_digest IS NULL
     OR p_last_round_digest !~ '^[0-9a-f]{64}$'
     OR p_observed_at IS NULL
     OR p_observed_at > v_now
     OR p_observed_at < v_now - interval '10 minutes'
  THEN
    RETURN jsonb_build_object('committed',false);
  END IF;
  IF p_changes IS NULL OR jsonb_typeof(p_changes)<>'array' THEN
    RETURN jsonb_build_object('committed',false);
  END IF;
  IF jsonb_array_length(p_changes)>5000 THEN
    RETURN jsonb_build_object('committed',false);
  END IF;

  FOR v_change IN SELECT value FROM jsonb_array_elements(p_changes) AS data(value) LOOP
    IF jsonb_typeof(v_change)<>'object'
       OR NOT (v_change ? 'event_ref')
       OR NOT (v_change ? 'kind')
       OR NOT (v_change ? 'thread_ref')
       OR v_change - 'event_ref' - 'kind' - 'thread_ref' <> '{}'::jsonb
       OR (v_change->>'event_ref') !~ '^[0-9a-f]{64}$'
       OR (v_change->>'kind') NOT IN (
         'added','deleted','label_added','label_removed','updated_or_added'
       )
       OR (
         v_change->>'thread_ref' IS NOT NULL
         AND (v_change->>'thread_ref') !~ '^[0-9a-f]{64}$'
       )
    THEN
      RETURN jsonb_build_object('committed',false);
    END IF;
  END LOOP;
  IF (
    SELECT count(DISTINCT value->>'event_ref')
    FROM jsonb_array_elements(p_changes) AS data(value)
  ) <> jsonb_array_length(p_changes) THEN
    RETURN jsonb_build_object('committed',false);
  END IF;

  -- The grant/verified-case source is queried AND locked by the DB in this
  -- transaction, independently of untrusted caller-provided booleans.
  IF NOT public.lola_delta_scope_allowed(
    p_company_id,p_employee_id,p_provider,p_provider_connection_ref,
    p_provider_identity_ref,p_mailbox_ref,p_folder_id,p_mapping_revision
  ) THEN
    RETURN jsonb_build_object('committed',false);
  END IF;

  SELECT * INTO checkpoint FROM public.lola_delta_checkpoints AS c
  WHERE c.company_id=p_company_id AND c.employee_id=p_employee_id
    AND c.provider=p_provider
    AND c.provider_connection_ref=p_provider_connection_ref
    AND c.provider_identity_ref=p_provider_identity_ref
    AND c.mailbox_ref=p_mailbox_ref
    AND c.folder_key=v_folder_key
    AND c.mapping_revision=p_mapping_revision
  FOR UPDATE;

  IF FOUND THEN
    IF checkpoint.version<>p_expected_version
       OR checkpoint.cursor_digest<>p_expected_cursor_digest THEN
      RETURN jsonb_build_object('committed',false);
    END IF;
    v_id:=checkpoint.id;
    v_version:=checkpoint.version+1;
    UPDATE public.lola_delta_checkpoints
    SET cursor_ciphertext=p_terminal_cursor_ciphertext,
        cursor_digest=p_terminal_cursor_digest,
        last_round_digest=p_last_round_digest,
        changes_applied=jsonb_array_length(p_changes),
        version=v_version,committed_at=clock_timestamp()
    WHERE id=v_id;
  ELSE
    IF p_expected_version<>0 OR p_provider<>'outlook' THEN
      RETURN jsonb_build_object('committed',false);
    END IF;
    INSERT INTO public.lola_delta_checkpoints(
       company_id,employee_id,provider,provider_connection_ref,
       provider_identity_ref,mailbox_ref,folder_key,mapping_revision,
       version,cursor_ciphertext,cursor_digest,last_round_digest,
       changes_applied,committed_at
    ) VALUES (
       p_company_id,p_employee_id,p_provider,p_provider_connection_ref,
       p_provider_identity_ref,p_mailbox_ref,v_folder_key,p_mapping_revision,
       1,p_terminal_cursor_ciphertext,p_terminal_cursor_digest,
       p_last_round_digest,jsonb_array_length(p_changes),clock_timestamp()
    ) ON CONFLICT ON CONSTRAINT lola_delta_checkpoint_exact_scope
      DO NOTHING
    RETURNING id,version INTO v_id,v_version;
    IF v_id IS NULL THEN
      RETURN jsonb_build_object('committed',false);
    END IF;
  END IF;

  -- Unexpected duplicate event_refs are hard failures (no DO NOTHING).
  -- PostgreSQL rolls the entire function call/statement back, including
  -- the cursor update, rather than marking partial evidence as complete.
  FOR v_change IN SELECT value FROM jsonb_array_elements(p_changes) AS data(value) LOOP
    INSERT INTO public.lola_delta_event_refs(
      checkpoint_id,event_ref,kind,thread_ref,committed_version
    ) VALUES(
      v_id,v_change->>'event_ref',v_change->>'kind',
      v_change->>'thread_ref',v_version
    );
  END LOOP;
  RETURN jsonb_build_object('committed',true);
END
$commit$;

-- PostgreSQL grants EXECUTE on new functions to PUBLIC by default. Revoke
-- ALL and re-grant only trusted service_role on each exact signature.
REVOKE ALL ON FUNCTION
  public.lola_delta_scope_allowed(uuid,uuid,text,text,text,text,text,text)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION
  public.lola_delta_checkpoint_read(uuid,uuid,text,text,text,text,text,text)
  FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION
  public.lola_delta_checkpoint_commit(
    uuid,uuid,text,text,text,text,text,text,integer,text,text,text,text,jsonb,timestamptz
  ) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION
  public.lola_delta_scope_allowed(uuid,uuid,text,text,text,text,text,text),
  public.lola_delta_checkpoint_read(uuid,uuid,text,text,text,text,text,text),
  public.lola_delta_checkpoint_commit(
    uuid,uuid,text,text,text,text,text,text,integer,text,text,text,text,jsonb,timestamptz
  ) TO service_role;

COMMIT;
