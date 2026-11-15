-- Disposable PostgreSQL only. Executed by CI after the legacy baseline,
-- Stage 8 replay schema and Stage 10 registry/write guard are applied.
-- All rows are synthetic. No hosted Supabase access or actual credentials.
\set ON_ERROR_STOP on
BEGIN;
-- Supabase provides auth.role() in hosted projects. Mirror it for CI.
CREATE OR REPLACE FUNCTION auth.role()
RETURNS text LANGUAGE sql STABLE
AS $$ SELECT current_setting('request.jwt.claim.role', true) $$;
GRANT USAGE ON SCHEMA public TO service_role;
-- Managed Supabase grants existing service clients read access to user
-- membership. Mirror that existing condition only in the throwaway DB.
-- PostgreSQL SELECT FOR SHARE requires UPDATE privilege on locked rows;
-- the managed service role already owns this capacity, emulate it in CI.
GRANT SELECT,UPDATE ON public.users TO service_role;

DO $verify$
BEGIN
    IF has_table_privilege('anon',
       'public.lola_channel_authorizations','SELECT')
       OR has_table_privilege('authenticated',
       'public.lola_channel_authorizations','SELECT')
       OR has_table_privilege('anon',
       'public.lola_verified_case_links','SELECT')
       OR has_table_privilege('authenticated',
       'public.lola_authorized_email_resources','INSERT')
       OR has_table_privilege('authenticated',
       'public.lola_provider_replay_checkpoints','SELECT')
       OR has_function_privilege('authenticated',
         'public.lola_commit_provider_replay(uuid,uuid,text,text,text,text,bigint,text,text,text,boolean,timestamptz,jsonb)',
         'EXECUTE')
    THEN
        RAISE EXCEPTION 'Browser role unexpectedly has Lola private source access';
    END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class
            WHERE oid='public.lola_channel_authorizations'::regclass)
       OR NOT (SELECT relrowsecurity FROM pg_class
            WHERE oid='public.lola_verified_case_links'::regclass)
       OR NOT (SELECT relrowsecurity FROM pg_class
            WHERE oid='public.lola_authorized_email_resources'::regclass)
    THEN
        RAISE EXCEPTION 'Lola verified registry RLS missing';
    END IF;
END
$verify$;

INSERT INTO public.companies(id,name)
VALUES ('11111111-1111-4111-8111-111111111111','CI synthetic tenant');
INSERT INTO public.users(id,company_id,email,hashed_password,role,is_active)
VALUES (
  '22222222-2222-4222-8222-222222222222',
  '11111111-1111-4111-8111-111111111111',
  'stage10-ci@invalid.example','synthetic-only','sales_rep',TRUE
);

-- DB-level lack of authorization must reject an INSERT, even when a
-- privileged caller tried to use the Stage 8 table directly.
DO $verify$
DECLARE rejected BOOLEAN := FALSE;
BEGIN
  BEGIN
    INSERT INTO public.lola_provider_replay_checkpoints(
      company_id,employee_id,provider,provider_identity_sha256,resource_sha256,
      mapping_revision,cursor_ciphertext,cursor_sha256,replay_digest,
      baseline_only,replayed_through,generation
    ) VALUES (
      '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222',
      'gmail', repeat('a',64),repeat('b',64),
      repeat('c',64),repeat('d',64),repeat('e',64),repeat('f',64),
      TRUE,now(),1
    );
  EXCEPTION WHEN OTHERS THEN
    rejected := position('authorization' in SQLERRM)>0;
  END;
  IF NOT rejected THEN
    RAISE EXCEPTION 'Replay checkpoint write without consent was allowed';
  END IF;
END
$verify$;

-- Populate a fully verified synthetic row set through service-owned tables.
-- The registry has NO default or invented verified identity records.
INSERT INTO public.lola_channel_authorizations (
    company_id,employee_id,channel,provider_name,provider_identity_ref,
    provider_identity_sha256,provider_verification_state,
    connector_authorization_state,employee_consent_at,
    provider_verified_at,authorization_expires_at,source_revision
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    'email','gmail','synthetic-provider-mailbox-1',repeat('a',64),
    'verified','connected',now()-interval '1 day',
    now()-interval '1 day',now()+interval '20 days','email:v1'
),(
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    'whatsapp_business','meta_whatsapp_business','synthetic-wa-number-1',
    repeat('f',64),'verified','connected',now()-interval '1 day',
    now()-interval '1 day',now()+interval '20 days','whatsapp:v1'
);
INSERT INTO public.lola_verified_case_links (
    company_id,employee_id,contact_id,work_item_id,
    email_provider_contact_ref,whatsapp_provider_contact_ref,
    link_verification_state,verification_source,employee_confirmed_at,
    link_verified_at,verification_expires_at,mapping_revision
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    'synthetic-contact','synthetic-label-project',
    'synthetic-email-contact','synthetic-wa-contact',
    'verified','provider_plus_employee',now()-interval '1 day',
    now()-interval '1 day',now()+interval '20 days','map:v1'
);
INSERT INTO public.lola_authorized_email_resources (
    company_id,employee_id,provider_name,
    provider_identity_sha256,resource_sha256,
    verified_at,authorization_expires_at,verification_source
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    'gmail',repeat('a',64),repeat('b',64),
    now()-interval '1 day',now()+interval '20 days',
    'oauth_principal_and_employee'
);

-- The DB-computed hash must match Stage 5's newline-joined input contract.
-- The same SQL expression is used only as test data; input remains synthetic.
CREATE TEMP TABLE stage10_fingerprint(fingerprint text NOT NULL);
INSERT INTO stage10_fingerprint(fingerprint)
SELECT encode(sha256(convert_to(concat_ws(E'\n',
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'synthetic-contact','synthetic-label-project','map:v1',
  'email:email:v1','whatsapp_business:whatsapp:v1'
),'UTF8')),'hex');

-- Synthetic fingerprint is test data only; grant its read scope to the
-- disposable service role before switching the active database role.
GRANT SELECT ON stage10_fingerprint TO service_role;
-- Authorize the request as the service role in the isolated database.
SET LOCAL ROLE service_role;
SET LOCAL request.jwt.claim.role = 'service_role';

-- Stage 8 atomic RPC creates both the encrypted checkpoint and source event.
DO $verify$
DECLARE v_generation BIGINT;
BEGIN
  SELECT public.lola_commit_provider_replay(
    '11111111-1111-4111-8111-111111111111'::uuid,
    '22222222-2222-4222-8222-222222222222'::uuid,
    'gmail',repeat('a',64),repeat('b',64),
    (SELECT fingerprint FROM stage10_fingerprint),
    0,repeat('z',70),repeat('d',64),repeat('e',64),
    TRUE,now(),
    jsonb_build_array(jsonb_build_object(
      'key_sha256',repeat('c',64),'operation','added'))
  ) INTO v_generation;
  IF v_generation != 1 THEN
    RAISE EXCEPTION 'Atomic Lola replay bootstrap generation invalid';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.lola_provider_replay_events
    WHERE event_key_sha256=repeat('c',64)
      AND company_id='11111111-1111-4111-8111-111111111111'::uuid
      AND employee_id='22222222-2222-4222-8222-222222222222'::uuid
  ) THEN
    RAISE EXCEPTION 'Atomic Lola replay event was not committed';
  END IF;
END
$verify$;

-- SQL revocation (concurrent real transactions must lock these same rows).
UPDATE public.lola_channel_authorizations
SET employee_consent_revoked_at=now()
WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
AND employee_id='22222222-2222-4222-8222-222222222222'::uuid
AND channel='whatsapp_business';

DO $verify$
DECLARE rejected BOOLEAN := FALSE;
DECLARE previous_generation BIGINT;
BEGIN
  SELECT generation INTO previous_generation
  FROM public.lola_provider_replay_checkpoints
  WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
    AND employee_id='22222222-2222-4222-8222-222222222222'::uuid;
  BEGIN
    PERFORM public.lola_commit_provider_replay(
      '11111111-1111-4111-8111-111111111111'::uuid,
      '22222222-2222-4222-8222-222222222222'::uuid,
      'gmail',repeat('a',64),repeat('b',64),
      (SELECT fingerprint FROM stage10_fingerprint),
      previous_generation, repeat('x',70),repeat('d',64),
      repeat('e',64),FALSE,now(),'[]'::jsonb
    );
  EXCEPTION WHEN OTHERS THEN
    rejected := position('consent' in SQLERRM)>0;
  END;
  IF NOT rejected THEN
    RAISE EXCEPTION 'Revoked employee consent did not reject replay commit';
  END IF;
  IF (SELECT generation FROM public.lola_provider_replay_checkpoints
      WHERE company_id='11111111-1111-4111-8111-111111111111'::uuid
        AND employee_id='22222222-2222-4222-8222-222222222222'::uuid
      LIMIT 1) != previous_generation
  THEN
    RAISE EXCEPTION 'Revoked replay advanced the cursor';
  END IF;
END
$verify$;

RESET ROLE;
ROLLBACK;
\echo 'Lola Stage 10 disposable PostgreSQL authorization replay tests passed'
