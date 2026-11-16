-- Disposable PostgreSQL Stage 13: resumable Gmail snapshot.
-- Synthetic only. Never run on hosted Supabase; CI PGDATABASE must be sai.
\set ON_ERROR_STOP on
BEGIN;
CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE
AS $$ SELECT current_setting('request.jwt.claim.role', true) $$;
GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT,UPDATE ON public.users TO service_role;

DO $assert$
BEGIN
  IF NOT (SELECT relrowsecurity FROM pg_class
          WHERE oid='public.lola_provider_replay_recoveries'::regclass)
     OR has_table_privilege('anon','public.lola_provider_replay_recoveries','SELECT')
     OR has_table_privilege('authenticated','public.lola_provider_replay_recoveries','SELECT')
     OR has_table_privilege('service_role','public.lola_provider_replay_recoveries','DELETE')
     OR has_function_privilege('authenticated',
       'public.lola_recover_expired_gmail_replay(uuid,uuid,text,text,text,bigint,text,text,text,text,timestamptz,integer,jsonb,text)',
       'EXECUTE')
     OR has_function_privilege('anon',
       'public.lola_recover_expired_gmail_replay(uuid,uuid,text,text,text,bigint,text,text,text,text,timestamptz,integer,jsonb,text)',
       'EXECUTE')
  THEN RAISE EXCEPTION 'Gmail recovery ledger privileges exposed'; END IF;
END
$assert$;

INSERT INTO public.companies(id,name)
VALUES ('11111111-1111-4111-8111-111111111111','Lola synthetic snapshot');
INSERT INTO public.users(id,company_id,email,hashed_password,role,is_active)
VALUES (
  '22222222-2222-4222-8222-222222222222',
  '11111111-1111-4111-8111-111111111111',
  'stage13-ci@invalid.example','synthetic-only','sales_rep',TRUE
);
INSERT INTO public.lola_channel_authorizations(
  company_id,employee_id,channel,provider_name,
  provider_identity_ref,provider_identity_sha256,
  provider_verification_state,connector_authorization_state,
  employee_consent_at,provider_verified_at,authorization_expires_at,source_revision
) VALUES (
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'email','gmail','synthetic-mailbox',repeat('a',64),
  'verified','connected',now()-interval '1 day',now()-interval '1 day',
  now()+interval '3 days','email:v1'
),(
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'whatsapp_business','meta_whatsapp_business','synthetic-wa',repeat('f',64),
  'verified','connected',now()-interval '1 day',now()-interval '1 day',
  now()+interval '3 days','wa:v1'
);
INSERT INTO public.lola_verified_case_links(
  company_id,employee_id,contact_id,work_item_id,
  email_provider_contact_ref,whatsapp_provider_contact_ref,
  link_verification_state,verification_source,employee_confirmed_at,
  link_verified_at,verification_expires_at,mapping_revision
) VALUES (
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'contact-1','label-project','synthetic-email-contact','synthetic-wa-contact',
  'verified','provider_plus_employee',now()-interval '1 day',
  now()-interval '1 day',now()+interval '3 days','map:v1'
);
INSERT INTO public.lola_authorized_email_resources(
  company_id,employee_id,provider_name,provider_identity_sha256,
  resource_sha256,verified_at,authorization_expires_at,verification_source
) VALUES (
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'gmail',repeat('a',64),repeat('b',64),now()-interval '1 day',
  now()+interval '3 days','oauth_principal_and_employee'
);

CREATE TEMP TABLE stage13_fingerprint(fingerprint text NOT NULL);
INSERT INTO stage13_fingerprint SELECT encode(sha256(convert_to(concat_ws(E'\n',
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'contact-1','label-project','map:v1',
  'email:email:v1','whatsapp_business:wa:v1'
),'UTF8')),'hex');
GRANT SELECT ON stage13_fingerprint TO service_role;

SET LOCAL ROLE service_role;
SET LOCAL request.jwt.claim.role = 'service_role';


-- Neither browser role can inspect staged metadata.
DO $verify$
BEGIN
  IF NOT (SELECT relrowsecurity FROM pg_class
    WHERE oid='public.lola_gmail_snapshot_jobs'::regclass)
     OR NOT (SELECT relrowsecurity FROM pg_class
    WHERE oid='public.lola_gmail_snapshot_pages'::regclass)
     OR NOT (SELECT relrowsecurity FROM pg_class
    WHERE oid='public.lola_gmail_snapshot_refs'::regclass)
     OR has_table_privilege('anon','public.lola_gmail_snapshot_jobs','SELECT')
     OR has_table_privilege('authenticated','public.lola_gmail_snapshot_refs','SELECT')
     OR has_function_privilege('anon',
       'public.lola_stage_gmail_snapshot_page(uuid,uuid,text,text,text,integer,text,text,text,text,text,text,jsonb,boolean)',
       'EXECUTE')
  THEN RAISE EXCEPTION 'Gmail snapshot metadata accidentally public'; END IF;
END
$verify$;

-- Insert one 100% encrypted first-page continuation into Stage 13.
DO $verify$
DECLARE n INTEGER;
BEGIN
  SELECT public.lola_stage_gmail_snapshot_page(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage13_fingerprint),
    0,NULL,
    'v1:'||repeat('H',68),repeat('h',64),
    'v1:'||repeat('T',68),repeat('t',64),
    repeat('1',64),
    jsonb_build_array(jsonb_build_object(
      'message_key_sha256',repeat('m',64),
      'thread_key_sha256',repeat('n',64),
      'metadata_history_key_sha256',repeat('q',64)
    )),FALSE
  ) INTO n;
  IF n<>1 THEN RAISE EXCEPTION 'First Gmail snapshot page not recorded'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_gmail_snapshot_jobs
    WHERE pages_staged=1 AND messages_staged=1 AND state='collecting'
      AND next_page_sha256=repeat('t',64)
  ) THEN RAISE EXCEPTION 'No durable encrypted Gmail continuation'; END IF;
END
$verify$;

-- Stale expected-generation must not advance the cursor.
DO $verify$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    PERFORM public.lola_stage_gmail_snapshot_page(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage13_fingerprint),
      0,NULL,
      'v1:'||repeat('H',68),repeat('h',64),
      NULL,NULL,repeat('2',64),'[]'::jsonb,TRUE
    );
  EXCEPTION WHEN OTHERS THEN
    rejected:=position('compare-and-swap' in SQLERRM)>0;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Stale Gmail snapshot resume accepted'; END IF;
END
$verify$;

-- The duplicate ref across a prior page aborts THE WHOLE next-page write.
DO $verify$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    PERFORM public.lola_stage_gmail_snapshot_page(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage13_fingerprint),
      1,repeat('t',64),
      'v1:'||repeat('H',68),repeat('h',64),
      NULL,NULL,repeat('2',64),
      jsonb_build_array(jsonb_build_object(
        'message_key_sha256',repeat('m',64),
        'thread_key_sha256',repeat('n',64),
        'metadata_history_key_sha256',repeat('q',64)
      )),TRUE
    );
  EXCEPTION WHEN unique_violation THEN rejected:=TRUE; END;
  IF NOT rejected THEN RAISE EXCEPTION 'Cross-page duplicate Gmail message accepted'; END IF;
  IF (SELECT pages_staged FROM public.lola_gmail_snapshot_jobs LIMIT 1)<>1 THEN
    RAISE EXCEPTION 'Duplicate message partially advanced checkpoint'; END IF;
END
$verify$;

-- Genuine different second page ends the snapshot but DOES NOT commit
-- the Gmail history cursor or authorize any customer follow-up.
DO $verify$
DECLARE n INTEGER;
BEGIN
  SELECT public.lola_stage_gmail_snapshot_page(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage13_fingerprint),
    1,repeat('t',64),
    'v1:'||repeat('H',68),repeat('h',64),
    NULL,NULL,repeat('2',64),
    jsonb_build_array(jsonb_build_object(
      'message_key_sha256',repeat('z',64),
      'thread_key_sha256',NULL,
      'metadata_history_key_sha256',repeat('q',64)
    )),TRUE
  ) INTO n;
  IF n<>2 THEN RAISE EXCEPTION 'Gmail snapshot final page lost'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_gmail_snapshot_jobs
    WHERE pages_staged=2 AND messages_staged=2
      AND state='snapshot_exhausted'
      AND next_page_ciphertext IS NULL AND next_page_sha256 IS NULL
  ) OR (SELECT count(*) FROM public.lola_gmail_snapshot_pages)<>2
     OR (SELECT count(*) FROM public.lola_gmail_snapshot_refs)<>2
  THEN RAISE EXCEPTION 'Gmail staged snapshot receipts not exact'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.lola_provider_replay_checkpoints
    WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
  ) THEN RAISE EXCEPTION 'Unsafe final Gmail cursor advanced before reconciliation'; END IF;
END
$verify$;

-- Consent revocation prevents ANY snapshot job update at the transaction gate.
UPDATE public.lola_channel_authorizations
SET employee_consent_revoked_at=now()
WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
  AND employee_id='22222222-2222-4222-8222-222222222222'::uuid
  AND channel='whatsapp_business';

DO $verify$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    UPDATE public.lola_gmail_snapshot_jobs
    SET updated_at=now()
    WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid;
  EXCEPTION WHEN OTHERS THEN rejected:=position('consent' in SQLERRM)>0; END;
  IF NOT rejected THEN RAISE EXCEPTION 'Snapshot write escaped revoked consent'; END IF;
  IF (SELECT pages_staged FROM public.lola_gmail_snapshot_jobs LIMIT 1)<>2 THEN
    RAISE EXCEPTION 'Revoked employee advanced Gmail snapshot'; END IF;
END
$verify$;
RESET ROLE;
ROLLBACK;
\echo 'Lola Stage 13 disposable PostgreSQL paged snapshot checks passed'
