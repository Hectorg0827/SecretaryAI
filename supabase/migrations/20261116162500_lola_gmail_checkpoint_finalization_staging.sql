-- Lola Stage 14B: service-role-only, tenant-scoped consistent Gmail snapshot
-- reader and atomic baseline finalization. UNAPPLIED STAGING CANDIDATE.
-- Requires Stage 8, 10, 12, 13 migrations. No hosted/project changes.
-- Encrypted Gmail cursor is an incremental baseline ONLY; never proof of
-- historical reply absence or permission to message a contact.
BEGIN;

CREATE TABLE public.lola_gmail_snapshot_finalizations (
  company_id UUID NOT NULL,
  employee_id UUID NOT NULL,
  provider TEXT NOT NULL DEFAULT 'gmail' CHECK (provider='gmail'),
  provider_identity_sha256 TEXT NOT NULL CHECK (provider_identity_sha256 ~ '^[0-9a-f]{64}$'),
  resource_sha256 TEXT NOT NULL CHECK (resource_sha256 ~ '^[0-9a-f]{64}$'),
  mapping_revision TEXT NOT NULL CHECK (length(btrim(mapping_revision)) BETWEEN 1 AND 200),
  initial_history_sha256 TEXT NOT NULL CHECK (initial_history_sha256 ~ '^[0-9a-f]{64}$'),
  snapshot_chain_sha256 TEXT NOT NULL CHECK (snapshot_chain_sha256 ~ '^[0-9a-f]{64}$'),
  snapshot_membership_sha256 TEXT NOT NULL CHECK (snapshot_membership_sha256 ~ '^[0-9a-f]{64}$'),
  history_digest_sha256 TEXT NOT NULL CHECK (history_digest_sha256 ~ '^[0-9a-f]{64}$'),
  reconciled_membership_sha256 TEXT NOT NULL CHECK (reconciled_membership_sha256 ~ '^[0-9a-f]{64}$'),
  cursor_sha256 TEXT NOT NULL CHECK (cursor_sha256 ~ '^[0-9a-f]{64}$'),
  replay_digest TEXT NOT NULL CHECK (replay_digest ~ '^[0-9a-f]{64}$'),
  checkpoint_generation BIGINT NOT NULL CHECK (checkpoint_generation=1),
  event_count INTEGER NOT NULL CHECK (event_count BETWEEN 0 AND 500),
  finalized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (company_id,employee_id,provider,provider_identity_sha256,resource_sha256),
  FOREIGN KEY (company_id,employee_id,provider,provider_identity_sha256,resource_sha256)
    REFERENCES public.lola_gmail_snapshot_jobs
    (company_id,employee_id,provider,provider_identity_sha256,resource_sha256)
    ON DELETE RESTRICT
);
ALTER TABLE public.lola_gmail_snapshot_finalizations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.lola_gmail_snapshot_finalizations
  FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT ON public.lola_gmail_snapshot_finalizations TO service_role;

-- Revalidate active employee, BOTH employee-controlled channels, exact Gmail
-- identity/resource and work-item mapping. Shared row locks prevent a consent
-- revocation racing the read/finalization transaction. No SECURITY DEFINER.
CREATE FUNCTION public.lola_require_gmail_snapshot_access(
  p_company UUID, p_employee UUID, p_identity TEXT, p_resource TEXT,
  p_mapping TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $body$
DECLARE
  v_user UUID;
  v_email public.lola_channel_authorizations%ROWTYPE;
  v_wa public.lola_channel_authorizations%ROWTYPE;
  v_resource BOOLEAN;
  v_contact TEXT;
  v_at TIMESTAMPTZ := transaction_timestamp();
BEGIN
  IF current_user IS DISTINCT FROM 'service_role'
    OR current_setting('request.jwt.claim.role',TRUE) IS DISTINCT FROM 'service_role'
  THEN RAISE EXCEPTION 'Gmail private snapshot reader unavailable'; END IF;
  IF p_company IS NULL OR p_employee IS NULL
    OR p_identity !~ '^[0-9a-f]{64}$'
    OR p_resource !~ '^[0-9a-f]{64}$'
    OR length(btrim(p_mapping)) NOT BETWEEN 1 AND 200
  THEN RAISE EXCEPTION 'Gmail private snapshot scope invalid'; END IF;

  SELECT u.id INTO v_user FROM public.users u
  WHERE u.company_id=p_company AND u.id=p_employee AND u.is_active IS TRUE
    AND u.role IN ('owner','manager','back_office','sales_rep')
  FOR SHARE;
  IF v_user IS NULL THEN RAISE EXCEPTION 'Gmail employee inactive or unauthorized'; END IF;

  SELECT a.* INTO v_email FROM public.lola_channel_authorizations a
  WHERE a.company_id=p_company AND a.employee_id=p_employee AND a.channel='email'
  FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Gmail email consent unavailable'; END IF;
  SELECT a.* INTO v_wa FROM public.lola_channel_authorizations a
  WHERE a.company_id=p_company AND a.employee_id=p_employee
    AND a.channel='whatsapp_business'
  FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Gmail WhatsApp consent unavailable'; END IF;
  IF v_email.provider_name IS DISTINCT FROM 'gmail'
    OR v_email.provider_identity_sha256 IS DISTINCT FROM p_identity
    OR v_email.provider_verification_state IS DISTINCT FROM 'verified'
    OR v_email.connector_authorization_state IS DISTINCT FROM 'connected'
    OR v_email.employee_consent_at IS NULL OR v_email.employee_consent_at>v_at
    OR v_email.employee_consent_revoked_at IS NOT NULL
    OR v_email.provider_verified_at IS NULL OR v_email.provider_verified_at>v_at
    OR v_email.authorization_expires_at IS NULL OR v_email.authorization_expires_at<=v_at
    OR v_wa.provider_name IS DISTINCT FROM 'meta_whatsapp_business'
    OR v_wa.provider_verification_state IS DISTINCT FROM 'verified'
    OR v_wa.connector_authorization_state IS DISTINCT FROM 'connected'
    OR v_wa.employee_consent_at IS NULL OR v_wa.employee_consent_at>v_at
    OR v_wa.employee_consent_revoked_at IS NOT NULL
    OR v_wa.provider_verified_at IS NULL OR v_wa.provider_verified_at>v_at
    OR v_wa.authorization_expires_at IS NULL OR v_wa.authorization_expires_at<=v_at
  THEN RAISE EXCEPTION 'Gmail employee channel consent revoked or expired'; END IF;

  SELECT TRUE INTO v_resource FROM public.lola_authorized_email_resources r
  WHERE r.company_id=p_company AND r.employee_id=p_employee
    AND r.provider_name='gmail' AND r.provider_identity_sha256=p_identity
    AND r.resource_sha256=p_resource AND r.revoked_at IS NULL
    AND r.verified_at<=v_at AND r.authorization_expires_at>v_at
  FOR SHARE;
  IF v_resource IS DISTINCT FROM TRUE
  THEN RAISE EXCEPTION 'Gmail mailbox resource authorization unavailable'; END IF;

  SELECT l.contact_id INTO v_contact FROM public.lola_verified_case_links l
  WHERE l.company_id=p_company AND l.employee_id=p_employee
    AND l.link_verification_state='verified'
    AND l.verification_source='provider_plus_employee'
    AND l.employee_confirmed_at IS NOT NULL AND l.employee_confirmed_at<=v_at
    AND l.link_verified_at IS NOT NULL AND l.link_verified_at<=v_at
    AND l.link_revoked_at IS NULL AND l.verification_expires_at>v_at
    AND encode(sha256(convert_to(concat_ws(E'\n',
      l.company_id::text,l.employee_id::text,l.contact_id,l.work_item_id,
      l.mapping_revision,'email:'||v_email.source_revision,
      'whatsapp_business:'||v_wa.source_revision
    ),'UTF8')),'hex')=p_mapping
  LIMIT 1 FOR SHARE;
  IF v_contact IS NULL
  THEN RAISE EXCEPTION 'Gmail private case mapping unavailable'; END IF;
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_require_gmail_snapshot_access(
  UUID,UUID,TEXT,TEXT,TEXT
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_require_gmail_snapshot_access(
  UUID,UUID,TEXT,TEXT,TEXT
) TO service_role;

-- A single backend RPC returns one transactionally scoped bundle. The job
-- row is locked until the transaction ends; Stage 13 writers require an
-- UPDATE lock on this same row. Only exhausted, immutable snapshots are read.
-- No plaintext message ids, headers, OAuth tokens or provider cursors.
CREATE FUNCTION public.lola_read_gmail_snapshot_bundle(
  p_company_id UUID, p_employee_id UUID,
  p_provider_identity_sha256 TEXT, p_resource_sha256 TEXT,
  p_mapping_revision TEXT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $body$
DECLARE
  v_job public.lola_gmail_snapshot_jobs%ROWTYPE;
  v_pages JSONB;
  v_refs JSONB;
BEGIN
  PERFORM public.lola_require_gmail_snapshot_access(
    p_company_id,p_employee_id,p_provider_identity_sha256,p_resource_sha256,p_mapping_revision
  );
  SELECT * INTO v_job FROM public.lola_gmail_snapshot_jobs
  WHERE company_id=p_company_id AND employee_id=p_employee_id AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256 FOR SHARE;
  IF NOT FOUND OR v_job.mapping_revision IS DISTINCT FROM p_mapping_revision
    OR v_job.state IS DISTINCT FROM 'snapshot_exhausted'
    OR v_job.next_page_ciphertext IS NOT NULL OR v_job.next_page_sha256 IS NOT NULL
    OR v_job.pages_staged NOT BETWEEN 1 AND 500
    OR v_job.messages_staged NOT BETWEEN 0 AND 20000
  THEN RAISE EXCEPTION 'Complete Gmail snapshot unavailable'; END IF;

  -- Both arrays are read by ONE SQL statement, so their MVCC snapshot is
  -- identical even at READ COMMITTED; caller verifies full counts + chain.
  SELECT
    COALESCE((
      SELECT jsonb_agg(to_jsonb(p) ORDER BY p.page_index)
      FROM public.lola_gmail_snapshot_pages p
      WHERE p.company_id=p_company_id AND p.employee_id=p_employee_id
        AND p.provider='gmail' AND p.provider_identity_sha256=p_provider_identity_sha256
        AND p.resource_sha256=p_resource_sha256
    ),'[]'::jsonb),
    COALESCE((
      SELECT jsonb_agg(to_jsonb(r) ORDER BY r.page_index,r.message_key_sha256)
      FROM public.lola_gmail_snapshot_refs r
      WHERE r.company_id=p_company_id AND r.employee_id=p_employee_id
        AND r.provider='gmail' AND r.provider_identity_sha256=p_provider_identity_sha256
        AND r.resource_sha256=p_resource_sha256
    ),'[]'::jsonb)
  INTO v_pages,v_refs;
  IF jsonb_array_length(v_pages)<>v_job.pages_staged
     OR jsonb_array_length(v_refs)<>v_job.messages_staged
  THEN RAISE EXCEPTION 'Incomplete Gmail snapshot receipt set'; END IF;
  RETURN jsonb_build_object('job',to_jsonb(v_job),'pages',v_pages,'refs',v_refs);
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_read_gmail_snapshot_bundle(
  UUID,UUID,TEXT,TEXT,TEXT
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_read_gmail_snapshot_bundle(
  UUID,UUID,TEXT,TEXT,TEXT
) TO service_role;

-- Commit a stage-13 baseline exactly once. An identical retry returns 1
-- without producing a second event or checkpoint. Anything else fails closed.
-- Stage 10's INSERT trigger independently rechecks consent/resource/mapping
-- inside this transaction, under row locks until COMMIT.
CREATE FUNCTION public.lola_finalize_gmail_snapshot_baseline(
  p_company_id UUID, p_employee_id UUID,
  p_provider_identity_sha256 TEXT, p_resource_sha256 TEXT,
  p_mapping_revision TEXT, p_initial_history_sha256 TEXT,
  p_expected_pages INTEGER, p_expected_messages INTEGER,
  p_snapshot_chain_sha256 TEXT, p_snapshot_membership_sha256 TEXT,
  p_history_digest_sha256 TEXT, p_reconciled_membership_sha256 TEXT,
  p_cursor_ciphertext TEXT, p_cursor_sha256 TEXT, p_replay_digest TEXT,
  p_replayed_through TIMESTAMPTZ, p_events JSONB
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $body$
DECLARE
  v_job public.lola_gmail_snapshot_jobs%ROWTYPE;
  v_prior public.lola_gmail_snapshot_finalizations%ROWTYPE;
  v_page RECORD;
  v_ref_count BIGINT;
  v_ref_membership TEXT;
  v_page_count INTEGER:=0;
  v_record_total INTEGER:=0;
  v_chain TEXT:='';
  v_bad_count INTEGER;
  v_event JSONB;
  v_seen TEXT[]:=ARRAY[]::TEXT[];
  v_key TEXT;
  v_op TEXT;
BEGIN
  PERFORM public.lola_require_gmail_snapshot_access(
    p_company_id,p_employee_id,p_provider_identity_sha256,p_resource_sha256,p_mapping_revision
  );
  IF p_expected_pages NOT BETWEEN 1 AND 500
    OR p_expected_messages NOT BETWEEN 0 AND 20000
    OR p_initial_history_sha256 !~ '^[0-9a-f]{64}$'
    OR p_snapshot_chain_sha256 !~ '^[0-9a-f]{64}$'
    OR p_snapshot_membership_sha256 !~ '^[0-9a-f]{64}$'
    OR p_history_digest_sha256 !~ '^[0-9a-f]{64}$'
    OR p_reconciled_membership_sha256 !~ '^[0-9a-f]{64}$'
    OR p_cursor_ciphertext !~ '^v1:[A-Za-z0-9+/=]+$'
    OR length(p_cursor_ciphertext) NOT BETWEEN 40 AND 8192
    OR p_cursor_sha256 !~ '^[0-9a-f]{64}$'
    OR p_replay_digest !~ '^[0-9a-f]{64}$'
    OR p_replayed_through IS NULL OR p_replayed_through>now()+interval '5 minutes'
    OR p_events IS NULL OR jsonb_typeof(p_events) IS DISTINCT FROM 'array'
    OR jsonb_array_length(p_events)>500
  THEN RAISE EXCEPTION 'Gmail finalization evidence malformed'; END IF;

  -- Stage 13's writer obtains an UPDATE lock here; it cannot race finalization.
  SELECT * INTO v_job FROM public.lola_gmail_snapshot_jobs
  WHERE company_id=p_company_id AND employee_id=p_employee_id AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256 FOR UPDATE;
  IF NOT FOUND OR v_job.mapping_revision IS DISTINCT FROM p_mapping_revision
    OR v_job.state IS DISTINCT FROM 'snapshot_exhausted'
    OR v_job.next_page_ciphertext IS NOT NULL OR v_job.next_page_sha256 IS NOT NULL
    OR v_job.pages_staged IS DISTINCT FROM p_expected_pages
    OR v_job.messages_staged IS DISTINCT FROM p_expected_messages
    OR v_job.initial_history_sha256 IS DISTINCT FROM p_initial_history_sha256
    OR v_job.page_chain_sha256 IS DISTINCT FROM p_snapshot_chain_sha256
  THEN RAISE EXCEPTION 'Gmail finalization source revision changed'; END IF;

  -- Recheck the source receipts IN the commit transaction: no trust in the
  -- client-supplied page chain, membership count or HMAC reference count.
  FOR v_page IN SELECT page_index,page_digest_sha256,chain_digest_sha256,entry_count
    FROM public.lola_gmail_snapshot_pages
    WHERE company_id=p_company_id AND employee_id=p_employee_id AND provider='gmail'
      AND provider_identity_sha256=p_provider_identity_sha256
      AND resource_sha256=p_resource_sha256
    ORDER BY page_index
  LOOP
    IF v_page.page_index IS DISTINCT FROM v_page_count THEN
      RAISE EXCEPTION 'Gmail finalization missing snapshot page'; END IF;
    IF v_page_count=0 THEN
      v_chain:=encode(sha256(convert_to(concat_ws(':',
        'lola-gmail-snapshot-chain-v1',p_initial_history_sha256,v_page.page_digest_sha256
      ),'UTF8')),'hex');
    ELSE
      v_chain:=encode(sha256(convert_to(concat_ws(':',
        v_chain,v_page.page_digest_sha256
      ),'UTF8')),'hex');
    END IF;
    IF v_chain IS DISTINCT FROM v_page.chain_digest_sha256 THEN
      RAISE EXCEPTION 'Gmail finalization source chain invalid'; END IF;
    v_page_count:=v_page_count+1;
    v_record_total:=v_record_total+v_page.entry_count;
  END LOOP;
  IF v_page_count<>p_expected_pages OR v_chain<>p_snapshot_chain_sha256
     OR v_record_total<>p_expected_messages
  THEN RAISE EXCEPTION 'Gmail finalization page coverage incomplete'; END IF;

  SELECT count(*), encode(sha256(convert_to(
    COALESCE(string_agg(message_key_sha256,'' ORDER BY message_key_sha256),''),
    'UTF8')),'hex')
  INTO v_ref_count,v_ref_membership
  FROM public.lola_gmail_snapshot_refs
  WHERE company_id=p_company_id AND employee_id=p_employee_id AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256;
  IF v_ref_count<>p_expected_messages
    OR v_ref_membership<>p_snapshot_membership_sha256 THEN
    RAISE EXCEPTION 'Gmail snapshot HMAC membership changed'; END IF;

  SELECT count(*) INTO v_bad_count FROM (
    SELECT p.page_index
    FROM public.lola_gmail_snapshot_pages p
    LEFT JOIN public.lola_gmail_snapshot_refs r
      ON r.company_id=p.company_id AND r.employee_id=p.employee_id
      AND r.provider=p.provider AND r.provider_identity_sha256=p.provider_identity_sha256
      AND r.resource_sha256=p.resource_sha256 AND r.page_index=p.page_index
    WHERE p.company_id=p_company_id AND p.employee_id=p_employee_id
      AND p.provider='gmail' AND p.provider_identity_sha256=p_provider_identity_sha256
      AND p.resource_sha256=p_resource_sha256
    GROUP BY p.page_index,p.entry_count
    HAVING count(r.message_key_sha256)<>p.entry_count
  ) q;
  IF v_bad_count<>0 THEN RAISE EXCEPTION 'Gmail snapshot per-page references incomplete'; END IF;

  -- Refuse duplicate HMAC events, invalid operations, even on retried calls.
  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    v_key:=v_event->>'key_sha256';
    v_op:=v_event->>'operation';
    IF v_key !~ '^[0-9a-f]{64}$' OR v_op NOT IN
      ('added','deleted','label_changed')
      OR v_key=ANY(v_seen)
    THEN RAISE EXCEPTION 'Gmail finalization change evidence invalid'; END IF;
    v_seen:=array_append(v_seen,v_key);
  END LOOP;

  SELECT * INTO v_prior FROM public.lola_gmail_snapshot_finalizations
  WHERE company_id=p_company_id AND employee_id=p_employee_id AND provider='gmail'
    AND provider_identity_sha256=p_provider_identity_sha256
    AND resource_sha256=p_resource_sha256 FOR UPDATE;
  IF FOUND THEN
    IF v_prior.mapping_revision IS DISTINCT FROM p_mapping_revision
      OR v_prior.initial_history_sha256 IS DISTINCT FROM p_initial_history_sha256
      OR v_prior.snapshot_chain_sha256 IS DISTINCT FROM p_snapshot_chain_sha256
      OR v_prior.snapshot_membership_sha256 IS DISTINCT FROM p_snapshot_membership_sha256
      OR v_prior.history_digest_sha256 IS DISTINCT FROM p_history_digest_sha256
      OR v_prior.reconciled_membership_sha256 IS DISTINCT FROM p_reconciled_membership_sha256
      OR v_prior.cursor_sha256 IS DISTINCT FROM p_cursor_sha256
      OR v_prior.replay_digest IS DISTINCT FROM p_replay_digest
      OR v_prior.event_count IS DISTINCT FROM jsonb_array_length(p_events)
    THEN RAISE EXCEPTION 'Gmail finalization retry evidence conflict'; END IF;
    RETURN v_prior.checkpoint_generation;
  END IF;

  IF EXISTS (SELECT 1 FROM public.lola_provider_replay_checkpoints
    WHERE company_id=p_company_id AND employee_id=p_employee_id AND provider='gmail'
      AND provider_identity_sha256=p_provider_identity_sha256
      AND resource_sha256=p_resource_sha256) THEN
    RAISE EXCEPTION 'Gmail initial checkpoint already exists'; END IF;

  INSERT INTO public.lola_provider_replay_checkpoints(
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
    mapping_revision,cursor_ciphertext,cursor_sha256,replay_digest,baseline_only,
    replayed_through,generation,recovery_epoch
  ) VALUES (
    p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,p_resource_sha256,
    p_mapping_revision,p_cursor_ciphertext,p_cursor_sha256,p_replay_digest,TRUE,
    p_replayed_through,1,0
  ); -- Stage 10 BEFORE INSERT rechecks all consent/role/resource gates.

  FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
  LOOP
    INSERT INTO public.lola_provider_replay_events(
      company_id,employee_id,provider,provider_identity_sha256,
      resource_sha256,event_key_sha256,operation,replay_digest,recovery_epoch
    ) VALUES (
      p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,
      p_resource_sha256,v_event->>'key_sha256',v_event->>'operation',
      p_replay_digest,0
    );
    -- A unique collision aborts cursor + events + receipt as one transaction.
  END LOOP;
  INSERT INTO public.lola_gmail_snapshot_finalizations(
    company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
    mapping_revision,initial_history_sha256,snapshot_chain_sha256,
    snapshot_membership_sha256,history_digest_sha256,
    reconciled_membership_sha256,cursor_sha256,replay_digest,
    checkpoint_generation,event_count
  ) VALUES (
    p_company_id,p_employee_id,'gmail',p_provider_identity_sha256,p_resource_sha256,
    p_mapping_revision,p_initial_history_sha256,p_snapshot_chain_sha256,
    p_snapshot_membership_sha256,p_history_digest_sha256,
    p_reconciled_membership_sha256,p_cursor_sha256,p_replay_digest,
    1,jsonb_array_length(p_events)
  );
  RETURN 1;
END;
$body$;
REVOKE ALL ON FUNCTION public.lola_finalize_gmail_snapshot_baseline(
  UUID,UUID,TEXT,TEXT,TEXT,TEXT,INTEGER,INTEGER,TEXT,TEXT,TEXT,TEXT,
  TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB
) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.lola_finalize_gmail_snapshot_baseline(
  UUID,UUID,TEXT,TEXT,TEXT,TEXT,INTEGER,INTEGER,TEXT,TEXT,TEXT,TEXT,
  TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB
) TO service_role;
COMMIT;
