-- Disposable PostgreSQL Stage 12: verified Gmail 404 recovery + audit.
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
VALUES ('11111111-1111-4111-8111-111111111111','Lola synthetic recovery');
INSERT INTO public.users(id,company_id,email,hashed_password,role,is_active)
VALUES (
  '22222222-2222-4222-8222-222222222222',
  '11111111-1111-4111-8111-111111111111',
  'stage12-ci@invalid.example','synthetic-only','sales_rep',TRUE
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

CREATE TEMP TABLE stage12_fingerprint(fingerprint text NOT NULL);
INSERT INTO stage12_fingerprint SELECT encode(sha256(convert_to(concat_ws(E'\n',
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'contact-1','label-project','map:v1',
  'email:email:v1','whatsapp_business:wa:v1'
),'UTF8')),'hex');
GRANT SELECT ON stage12_fingerprint TO service_role;

SET LOCAL ROLE service_role;
SET LOCAL request.jwt.claim.role = 'service_role';

-- Seed a verified old checkpoint/event via Stage 8's original atomic writer.
DO $verify$
DECLARE generation BIGINT;
BEGIN
  SELECT public.lola_commit_provider_replay(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    'gmail',repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage12_fingerprint),0,
    repeat('q',70),repeat('d',64),repeat('e',64),
    TRUE,now(),jsonb_build_array(jsonb_build_object(
      'key_sha256',repeat('c',64),'operation','added'))
  ) INTO generation;
  IF generation<>1 THEN RAISE EXCEPTION 'Initial generation not one'; END IF;
END
$verify$;

-- New epoch must NOT be created by a stale generation or forged old cursor.
DO $verify$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    PERFORM public.lola_recover_expired_gmail_replay(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage12_fingerprint),
      9,repeat('d',64),'v1:'||repeat('X',68),repeat('f',64),
      repeat('9',64),now(),2,'[]'::jsonb,'gmail_history_404'
    );
  EXCEPTION WHEN OTHERS THEN
    rejected:=position('compare-and-swap' in SQLERRM)>0;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Stale recovery generation was accepted'; END IF;
END
$verify$;

-- The verified epoch update, new event, and audit row commit atomically.
DO $verify$
DECLARE generation BIGINT;
BEGIN
  SELECT public.lola_recover_expired_gmail_replay(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage12_fingerprint),1,
    repeat('d',64),'v1:'||repeat('X',68),repeat('f',64),
    repeat('9',64),now(),1,jsonb_build_array(jsonb_build_object(
      'key_sha256',repeat('1',64),'operation','added'
    )),'gmail_history_404'
  ) INTO generation;
  IF generation<>2 THEN RAISE EXCEPTION 'Expected reset to generation 2'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_provider_replay_checkpoints
    WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
      AND employee_id='22222222-2222-4222-8222-222222222222'::uuid
      AND generation=2 AND recovery_epoch=1 AND baseline_only IS TRUE
      AND cursor_sha256=repeat('f',64) AND replay_digest=repeat('9',64)
  ) THEN RAISE EXCEPTION 'Gmail reset cursor/epoch not written'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_provider_replay_events
    WHERE event_key_sha256=repeat('c',64) AND recovery_epoch=0
      AND replay_digest=repeat('e',64)
  ) THEN RAISE EXCEPTION 'Old replay event lost during reset'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_provider_replay_events
    WHERE event_key_sha256=repeat('1',64) AND recovery_epoch=1
      AND replay_digest=repeat('9',64)
  ) THEN RAISE EXCEPTION 'New replay event or epoch missing'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_provider_replay_recoveries
    WHERE old_generation=1 AND new_generation=2
      AND old_epoch=0 AND new_epoch=1
      AND old_cursor_sha256=repeat('d',64)
      AND new_cursor_sha256=repeat('f',64)
      AND old_replay_digest=repeat('e',64)
      AND new_replay_digest=repeat('9',64)
      AND reason='gmail_history_404'
      AND initial_message_count=1 AND new_event_count=1
  ) THEN RAISE EXCEPTION 'Recovery audit chain missing'; END IF;
END
$verify$;

-- An ordinary incremental batch AFTER recovery must inherit epoch 1,
-- never silently insert fresh changes into epoch zero. Roll back that
-- synthetic follow-on batch so other CAS tests still exercise generation 2.
SAVEPOINT stage12_follow_on;
DO $verify$
DECLARE generation BIGINT;
BEGIN
  SELECT public.lola_commit_provider_replay(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    'gmail',repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage12_fingerprint),2,
    'v1:'||repeat('Y',68),repeat('8',64),repeat('7',64),
    FALSE,now(),jsonb_build_array(jsonb_build_object(
      'key_sha256',repeat('2',64),'operation','label_changed'
    ))
  ) INTO generation;
  IF generation<>3 THEN RAISE EXCEPTION 'Follow-on replay generation wrong'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_provider_replay_events
    WHERE event_key_sha256=repeat('2',64)
      AND recovery_epoch=1 AND replay_digest=repeat('7',64)
  ) THEN RAISE EXCEPTION 'Ordinary post-recovery event was assigned prior epoch'; END IF;
END
$verify$;
ROLLBACK TO SAVEPOINT stage12_follow_on;

-- Reusing the same expected generation must fail closed.
DO $verify$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    PERFORM public.lola_recover_expired_gmail_replay(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage12_fingerprint),1,
      repeat('d',64),'v1:'||repeat('W',68),repeat('8',64),
      repeat('7',64),now(),1,'[]'::jsonb,'gmail_history_404'
    );
  EXCEPTION WHEN OTHERS THEN
    rejected:=position('compare-and-swap' in SQLERRM)>0;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Previously reset cursor replayed'; END IF;
END
$verify$;

UPDATE public.lola_channel_authorizations
SET employee_consent_revoked_at=now()
WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
AND employee_id='22222222-2222-4222-8222-222222222222'::uuid
AND channel='whatsapp_business';

-- Revocation must roll back the NEW epoch, cursor and audit atomically.
DO $verify$
DECLARE rejected BOOLEAN:=FALSE;
BEGIN
  BEGIN
    PERFORM public.lola_recover_expired_gmail_replay(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage12_fingerprint),2,
      repeat('f',64),'v1:'||repeat('Z',68),repeat('6',64),
      repeat('5',64),now(),0,'[]'::jsonb,'gmail_history_404'
    );
  EXCEPTION WHEN OTHERS THEN
    rejected:=position('consent' in SQLERRM)>0;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Revoked recovery advanced checkpoint'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.lola_provider_replay_checkpoints
    WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
      AND (recovery_epoch<>1 OR generation<>2)
  ) OR (SELECT COUNT(*) FROM public.lola_provider_replay_recoveries) <> 1 THEN
    RAISE EXCEPTION 'Rejected reset mutated audit history';
  END IF;
END
$verify$;

RESET ROLE;
ROLLBACK;
\echo 'Lola Stage12 disposable PostgreSQL audited Gmail recovery passed'
