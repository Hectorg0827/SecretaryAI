-- Stage 14B disposable Postgres security / idempotency acceptance.
-- This script MUST run only against CI's disposable 'sai' database.
\set ON_ERROR_STOP on
BEGIN;
DO $db$ BEGIN
  IF current_database()<>'sai' THEN
    RAISE EXCEPTION 'Stage14B CI-only SQL refuses non-sai database';
  END IF;
END $db$;

DO $security$
BEGIN
  IF NOT (SELECT relrowsecurity FROM pg_class
    WHERE oid='public.lola_gmail_snapshot_finalizations'::regclass)
    OR has_table_privilege('anon','public.lola_gmail_snapshot_finalizations','SELECT')
    OR has_table_privilege('authenticated','public.lola_gmail_snapshot_finalizations','INSERT')
    OR has_table_privilege('service_role','public.lola_gmail_snapshot_finalizations','DELETE')
    OR has_function_privilege('anon',
      'public.lola_read_gmail_snapshot_bundle(uuid,uuid,text,text,text)','EXECUTE')
    OR has_function_privilege('authenticated',
      'public.lola_finalize_gmail_snapshot_baseline(uuid,uuid,text,text,text,text,integer,integer,text,text,text,text,text,text,text,timestamptz,jsonb)',
      'EXECUTE')
  THEN RAISE EXCEPTION 'Stage14B source/cursor metadata exposed to browser roles'; END IF;
END $security$;
-- Disposable Postgres CI grants; rolled back with this entire fixture.
GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT,UPDATE ON public.users TO service_role;

INSERT INTO public.companies(id,name)
VALUES ('11111111-1111-4111-8111-111111111111','Stage 14B synthetic fixture');
CREATE TEMP TABLE stage14b_fingerprint(employee_id UUID PRIMARY KEY,fingerprint TEXT NOT NULL);

DO $seed$
DECLARE
  v_employee UUID;
  v_fingerprint TEXT;
BEGIN
  FOREACH v_employee IN ARRAY ARRAY[
    '22222222-2222-4222-8222-222222222222'::uuid,
    '33333333-3333-4333-8333-333333333333'::uuid
  ] LOOP
    INSERT INTO public.users(id,company_id,email,hashed_password,role,is_active)
    VALUES (v_employee,'11111111-1111-4111-8111-111111111111',
      v_employee::text||'@invalid.example','synthetic-only','sales_rep',TRUE);
    INSERT INTO public.lola_channel_authorizations(
      company_id,employee_id,channel,provider_name,
      provider_identity_ref,provider_identity_sha256,
      provider_verification_state,connector_authorization_state,
      employee_consent_at,provider_verified_at,authorization_expires_at,source_revision
    ) VALUES
      ('11111111-1111-4111-8111-111111111111',v_employee,
       'email','gmail','synthetic-mailbox',repeat('a',64),'verified','connected',
       now()-interval '1 day',now()-interval '1 day',now()+interval '3 days','email:v1'),
      ('11111111-1111-4111-8111-111111111111',v_employee,
       'whatsapp_business','meta_whatsapp_business','synthetic-wa',repeat('f',64),
       'verified','connected',now()-interval '1 day',now()-interval '1 day',
       now()+interval '3 days','wa:v1');
    INSERT INTO public.lola_verified_case_links(
      company_id,employee_id,contact_id,work_item_id,
      email_provider_contact_ref,whatsapp_provider_contact_ref,
      link_verification_state,verification_source,employee_confirmed_at,
      link_verified_at,verification_expires_at,mapping_revision
    ) VALUES (
      '11111111-1111-4111-8111-111111111111',v_employee,'contact-ci','work-ci',
      'synthetic-email','synthetic-wa','verified','provider_plus_employee',
      now()-interval '1 day',now()-interval '1 day',
      now()+interval '3 days','map:v1'
    );
    INSERT INTO public.lola_authorized_email_resources(
      company_id,employee_id,provider_name,provider_identity_sha256,
      resource_sha256,verified_at,authorization_expires_at,verification_source
    ) VALUES (
      '11111111-1111-4111-8111-111111111111',v_employee,'gmail',
      repeat('a',64),repeat('b',64),
      now()-interval '1 day',now()+interval '3 days',
      'oauth_principal_and_employee'
    );
    v_fingerprint:=encode(sha256(convert_to(concat_ws(E'\n',
      '11111111-1111-4111-8111-111111111111',v_employee::text,
      'contact-ci','work-ci','map:v1','email:email:v1',
      'whatsapp_business:wa:v1'),'UTF8')),'hex');
    INSERT INTO stage14b_fingerprint VALUES (v_employee,v_fingerprint);
  END LOOP;
END $seed$;
GRANT SELECT ON stage14b_fingerprint TO service_role;
SET LOCAL ROLE service_role;
SET LOCAL request.jwt.claim.role = 'service_role';

DO $stage$
DECLARE
  v_employee UUID;
  v_n INTEGER;
  v_msg TEXT;
BEGIN
  FOREACH v_employee IN ARRAY ARRAY[
    '22222222-2222-4222-8222-222222222222'::uuid,
    '33333333-3333-4333-8333-333333333333'::uuid
  ] LOOP
    v_msg:=CASE WHEN v_employee::text LIKE '222%' THEN repeat('c',64)
                ELSE repeat('d',64) END;
    SELECT public.lola_stage_gmail_snapshot_page(
      '11111111-1111-4111-8111-111111111111'::uuid,v_employee,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage14b_fingerprint WHERE employee_id=v_employee),
      0,NULL,'v1:'||repeat('H',68),repeat('1',64),
      NULL,NULL,repeat('2',64),
      jsonb_build_array(jsonb_build_object(
        'message_key_sha256',v_msg,
        'thread_key_sha256',NULL,
        'metadata_history_key_sha256',repeat('e',64)
      )),TRUE
    ) INTO v_n;
    IF v_n<>1 THEN RAISE EXCEPTION 'Gmail synthetic source page did not stage'; END IF;
  END LOOP;
END $stage$;

DO $read$
DECLARE
  v_first JSONB;
  v_second JSONB;
BEGIN
  SELECT public.lola_read_gmail_snapshot_bundle(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage14b_fingerprint
      WHERE employee_id='22222222-2222-4222-8222-222222222222'::uuid)
  ) INTO v_first;
  SELECT public.lola_read_gmail_snapshot_bundle(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '33333333-3333-4333-8333-333333333333'::uuid,
    repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage14b_fingerprint
      WHERE employee_id='33333333-3333-4333-8333-333333333333'::uuid)
  ) INTO v_second;
  IF jsonb_array_length(v_first->'pages')<>1
    OR jsonb_array_length(v_first->'refs')<>1
    OR v_first#>>'{refs,0,message_key_sha256}'<>repeat('c',64)
    OR v_second#>>'{refs,0,message_key_sha256}'<>repeat('d',64)
    OR v_first#>>'{job,employee_id}'='33333333-3333-4333-8333-333333333333'
  THEN RAISE EXCEPTION 'Employee-scoped Gmail snapshot bundle leaked or incomplete'; END IF;
END $read$;

-- A wrong mapping identity may not inspect even a known employee's source.
DO $cross$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    PERFORM public.lola_read_gmail_snapshot_bundle(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage14b_fingerprint
       WHERE employee_id='33333333-3333-4333-8333-333333333333'::uuid)
    );
  EXCEPTION WHEN OTHERS THEN rejected:=TRUE; END;
  IF NOT rejected THEN RAISE EXCEPTION 'Gmail private cross-employee mapping accepted'; END IF;
END $cross$;

-- The first verified baseline installs one checkpoint, one event, one receipt.
DO $commit$
DECLARE
  v_chain TEXT;
  v_generation BIGINT;
  v_fp TEXT;
  v_emp UUID:='22222222-2222-4222-8222-222222222222';
  v_membership TEXT;
BEGIN
  SELECT page_chain_sha256 INTO v_chain FROM public.lola_gmail_snapshot_jobs
  WHERE employee_id=v_emp;
  SELECT fingerprint INTO v_fp FROM stage14b_fingerprint WHERE employee_id=v_emp;
  v_membership:=encode(sha256(convert_to(repeat('c',64),'UTF8')),'hex');
  SELECT public.lola_finalize_gmail_snapshot_baseline(
    '11111111-1111-4111-8111-111111111111'::uuid,v_emp,
    repeat('a',64),repeat('b',64),v_fp,repeat('1',64),1,1,
    v_chain,v_membership,repeat('6',64),repeat('7',64),
    'v1:'||repeat('C',68),repeat('5',64),repeat('8',64),
    now(),jsonb_build_array(jsonb_build_object(
      'key_sha256',repeat('9',64),'operation','added'
    ))
  ) INTO v_generation;
  IF v_generation<>1 THEN RAISE EXCEPTION 'Gmail initial finalization failed'; END IF;
  SELECT public.lola_finalize_gmail_snapshot_baseline(
    '11111111-1111-4111-8111-111111111111'::uuid,v_emp,
    repeat('a',64),repeat('b',64),v_fp,repeat('1',64),1,1,
    v_chain,v_membership,repeat('6',64),repeat('7',64),
    'v1:'||repeat('C',68),repeat('5',64),repeat('8',64),
    now(),jsonb_build_array(jsonb_build_object(
      'key_sha256',repeat('9',64),'operation','added'
    ))
  ) INTO v_generation;
  IF v_generation<>1
    OR (SELECT count(*) FROM public.lola_provider_replay_checkpoints
       WHERE employee_id=v_emp)<>1
    OR (SELECT count(*) FROM public.lola_provider_replay_events
       WHERE employee_id=v_emp)<>1
    OR (SELECT count(*) FROM public.lola_gmail_snapshot_finalizations
       WHERE employee_id=v_emp)<>1
  THEN RAISE EXCEPTION 'Idempotent Gmail retry created duplicate writes'; END IF;
  IF NOT EXISTS (SELECT 1 FROM public.lola_provider_replay_checkpoints
    WHERE employee_id=v_emp AND baseline_only AND generation=1) THEN
    RAISE EXCEPTION 'Gmail finalization incorrectly certified baseline'; END IF;
END $commit$;

DO $conflict$
DECLARE rejected BOOLEAN:=FALSE;
  v_emp UUID:='22222222-2222-4222-8222-222222222222';
BEGIN
  BEGIN
    PERFORM public.lola_finalize_gmail_snapshot_baseline(
      '11111111-1111-4111-8111-111111111111'::uuid,v_emp,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage14b_fingerprint WHERE employee_id=v_emp),
      repeat('1',64),1,1,
      (SELECT page_chain_sha256 FROM public.lola_gmail_snapshot_jobs
       WHERE employee_id=v_emp),
      encode(sha256(convert_to(repeat('c',64),'UTF8')),'hex'),
      repeat('6',64),repeat('7',64),
      'v1:'||repeat('C',68),repeat('5',64),repeat('0',64),
      now(),'[]'::jsonb
    );
  EXCEPTION WHEN OTHERS THEN rejected:=TRUE; END;
  IF NOT rejected THEN RAISE EXCEPTION 'Conflicting Gmail finalization replay accepted'; END IF;
END $conflict$;

-- Revoking WhatsApp consent blocks the second employee's DB read and write.
UPDATE public.lola_channel_authorizations
SET employee_consent_revoked_at=now()
WHERE employee_id='33333333-3333-4333-8333-333333333333'::uuid
AND channel='whatsapp_business';

DO $revoked$
DECLARE rejected BOOLEAN:=FALSE; v_emp UUID:='33333333-3333-4333-8333-333333333333';
BEGIN
  BEGIN
    PERFORM public.lola_read_gmail_snapshot_bundle(
      '11111111-1111-4111-8111-111111111111'::uuid,v_emp,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage14b_fingerprint WHERE employee_id=v_emp)
    );
  EXCEPTION WHEN OTHERS THEN rejected:=TRUE; END;
  IF NOT rejected THEN RAISE EXCEPTION 'Revoked Gmail snapshot read was allowed'; END IF;

  rejected:=FALSE;
  BEGIN
    PERFORM public.lola_finalize_gmail_snapshot_baseline(
      '11111111-1111-4111-8111-111111111111'::uuid,v_emp,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage14b_fingerprint WHERE employee_id=v_emp),
      repeat('1',64),1,1,
      (SELECT page_chain_sha256 FROM public.lola_gmail_snapshot_jobs
       WHERE employee_id=v_emp),
      encode(sha256(convert_to(repeat('d',64),'UTF8')),'hex'),
      repeat('6',64),repeat('7',64),
      'v1:'||repeat('C',68),repeat('5',64),repeat('8',64),
      now(),'[]'::jsonb
    );
  EXCEPTION WHEN OTHERS THEN rejected:=TRUE; END;
  IF NOT rejected OR EXISTS (SELECT 1 FROM public.lola_provider_replay_checkpoints
    WHERE employee_id=v_emp) THEN
    RAISE EXCEPTION 'Revoked employee unexpectedly committed a Gmail cursor'; END IF;
END $revoked$;

RESET ROLE;
ROLLBACK;
\echo 'Lola Stage 14B disposable PostgreSQL checks passed'
