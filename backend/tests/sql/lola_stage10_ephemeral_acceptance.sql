-- E2E SQL CONTRACT TEST on ephemeral PostgreSQL only.
-- Prerequisites: lola_stage10_ephemeral_fixture.sql and the Stage 10 review SQL.
-- Never execute against any hosted Supabase instance.
\set ON_ERROR_STOP on

INSERT INTO public.companies(id)
VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
INSERT INTO public.users(id,company_id,role,is_active)
VALUES (
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','owner',true
);
INSERT INTO public.employee_integrations(
  id,company_id,user_id,provider,connected_at
) VALUES (
  'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','outlook',now()-interval '1 day'
);
INSERT INTO public.lola_private_contacts(id,company_id,user_id)
VALUES (
  'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
);
INSERT INTO public.lola_employee_whatsapp_connections(
  id,company_id,user_id,phone_number_id,status,meta_verified_at,
  employee_consented_at,history_sync_consented,policy_clearance_id
) VALUES (
  'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  'wa-phone-owned-1','active',now()-interval '1 day',
  now()-interval '1 day',true,'meta-policy-cleared-test'
);
INSERT INTO public.lola_channel_authorizations(
  company_id,employee_id,channel,provider_identity_ref,
  provider_verification_state,connector_authorization_state,
  employee_consent_at,provider_verified_at,authorization_expires_at,
  source_revision
) VALUES
(
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  'email','employee-verified-outlook',
  'verified','connected',now()-interval '1 day',
  now()-interval '1 day',now()+interval '1 day','auth-email-1'
),
(
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  'whatsapp_business','wa-phone-owned-1',
  'verified','connected',now()-interval '1 day',
  now()-interval '1 day',now()+interval '1 day','auth-wa-1'
);
INSERT INTO public.lola_verified_case_links(
  company_id,employee_id,contact_id,work_item_id,
  email_provider_contact_ref,whatsapp_provider_contact_ref,
  link_verification_state,verification_source,employee_confirmed_at,
  link_verified_at,verification_expires_at,mapping_revision
) VALUES (
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  'verified-case-1','email-contact-opaque','whatsapp-contact-opaque',
  'verified','provider_plus_employee',
  now()-interval '1 day',now()-interval '1 day',
  now()+interval '1 day','link-source-rev-1'
);

DO $preflight$
BEGIN
  IF has_function_privilege('anon',
     'public.lola_delta_checkpoint_read(uuid,uuid,text,text,text,text,text,text)', 'EXECUTE')
     OR has_function_privilege('authenticated',
     'public.lola_delta_checkpoint_commit(uuid,uuid,text,text,text,text,text,text,integer,text,text,text,text,jsonb,timestamptz)', 'EXECUTE')
     OR has_function_privilege('anon',
     'public.lola_delta_scope_allowed(uuid,uuid,text,text,text,text,text,text)', 'EXECUTE')
  THEN
    RAISE EXCEPTION 'Browser-executable sensitive checkpoint RPC';
  END IF;
  IF has_table_privilege('anon','public.lola_delta_checkpoints','SELECT')
     OR has_table_privilege('authenticated','public.lola_delta_event_refs','INSERT')
     OR has_table_privilege('anon','public.lola_verified_case_links','SELECT')
  THEN
    RAISE EXCEPTION 'Browser can access employee-private checkpoint data';
  END IF;
  IF (
    SELECT count(*) FROM pg_catalog.pg_class
    WHERE oid IN (
      'public.lola_channel_authorizations'::regclass,
      'public.lola_verified_case_links'::regclass,
      'public.lola_delta_checkpoints'::regclass,
      'public.lola_delta_event_refs'::regclass
    ) AND relrowsecurity
  )<>4 THEN
    RAISE EXCEPTION 'All employee-private tables must have RLS enabled';
  END IF;
END
$preflight$;

-- Simulate the actual privileged server, not a browser/anon role.
SET ROLE service_role;

DO $accept$
DECLARE
  company uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  employee uuid := 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  mapping text;
  result jsonb;
  event_count integer;
BEGIN
  SELECT encode(extensions.digest(
    convert_to(
      concat_ws(E'\n',
        company::text,employee::text,
        'cccccccc-cccc-4ccc-8ccc-cccccccccccc','verified-case-1',
        'link-source-rev-1',
        'email:auth-email-1','whatsapp_business:auth-wa-1'
      ),'UTF8'),'sha256'),'hex'
  ) INTO mapping;
  IF NOT public.lola_delta_scope_allowed(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping
  ) THEN
    RAISE EXCEPTION 'Verified employee consent test setup did not authorize scope';
  END IF;

  SELECT public.lola_delta_checkpoint_read(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping
  ) INTO result;
  IF result IS NOT NULL THEN
    RAISE EXCEPTION 'Missing checkpoint should be NULL only after authorization';
  END IF;

  SELECT public.lola_delta_checkpoint_commit(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping,
    0,NULL,repeat('Q',64),repeat('b',64),repeat('c',64),
    jsonb_build_array(
      jsonb_build_object('event_ref',repeat('1',64),
         'kind','updated_or_added','thread_ref',repeat('7',64)),
      jsonb_build_object('event_ref',repeat('2',64),
         'kind','deleted','thread_ref',NULL)
    ),clock_timestamp()-interval '10 seconds'
  ) INTO result;
  IF result<> '{"committed":true}'::jsonb THEN
    RAISE EXCEPTION 'Atomic first folder checkpoint transaction failed';
  END IF;
  SELECT public.lola_delta_checkpoint_read(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping
  ) INTO result;
  IF (result->>'version')::integer<>1
     OR (result->>'changes_applied')::integer<>2
     OR result->>'cursor_digest'<>repeat('b',64)
     OR result->>'cursor_ciphertext'<>repeat('Q',64)
     OR result->>'events_and_cursor_single_transaction'<>'true'
     OR result->>'current_employee_authorization_checked'<>'true'
  THEN
    RAISE EXCEPTION 'Atomic primary readback mismatched stored cursor and events';
  END IF;

  SELECT count(*) INTO event_count
  FROM public.lola_delta_event_refs
  WHERE event_ref IN (repeat('1',64),repeat('2',64));
  IF event_count<>2 THEN
    RAISE EXCEPTION 'Atomic event rows not persisted';
  END IF;

  -- A second worker with expected_version=0 must lose without changes.
  SELECT public.lola_delta_checkpoint_commit(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping,
    0,NULL,repeat('R',64),repeat('d',64),repeat('e',64),
    '[]'::jsonb,clock_timestamp()-interval '10 seconds'
  ) INTO result;
  IF result<> '{"committed":false}'::jsonb THEN
    RAISE EXCEPTION 'Stale initial commit must be rejected';
  END IF;

  -- Duplicate source event in a new optimistic round must roll back
  -- the cursor change if the event insert violates the UNIQUE constraint.
  BEGIN
    PERFORM public.lola_delta_checkpoint_commit(
      company,employee,'outlook',
      'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      'employee-verified-outlook','mailbox-1','inbox',mapping,
      1,repeat('b',64),repeat('S',64),repeat('d',64),repeat('e',64),
      jsonb_build_array(
        jsonb_build_object('event_ref',repeat('1',64),
           'kind','updated_or_added','thread_ref',NULL)
      ),clock_timestamp()-interval '10 seconds'
    );
    RAISE EXCEPTION 'Duplicate source must trigger unique_violation rollback';
  EXCEPTION WHEN unique_violation THEN
    NULL;
  END;
  SELECT public.lola_delta_checkpoint_read(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping
  ) INTO result;
  IF (result->>'version')::integer<>1
     OR result->>'cursor_digest'<>repeat('b',64) THEN
    RAISE EXCEPTION 'Duplicate event partially advanced cursor';
  END IF;

  -- A distinct record with expected version 1 should advance atomically.
  SELECT public.lola_delta_checkpoint_commit(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping,
    1,repeat('b',64),repeat('S',64),repeat('d',64),repeat('e',64),
    jsonb_build_array(
      jsonb_build_object('event_ref',repeat('3',64),
         'kind','updated_or_added','thread_ref',NULL)
    ),clock_timestamp()-interval '10 seconds'
  ) INTO result;
  IF result<> '{"committed":true}'::jsonb THEN
    RAISE EXCEPTION 'Valid second version cursor advance failed';
  END IF;
  SELECT public.lola_delta_checkpoint_read(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping
  ) INTO result;
  IF (result->>'version')::integer<>2
     OR (result->>'changes_applied')::integer<>1
     OR result->>'cursor_digest'<>repeat('d',64) THEN
    RAISE EXCEPTION 'Second-version readback mismatch';
  END IF;

  -- Cross-company scope must be denied before returning any secret cursor.
  BEGIN
    PERFORM public.lola_delta_checkpoint_read(
      'ffffffff-ffff-4fff-8fff-ffffffffffff',employee,'outlook',
      'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      'employee-verified-outlook','mailbox-1','inbox',mapping
    );
    RAISE EXCEPTION 'Unowned company checkpoint read unexpectedly granted';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
END
$accept$;

RESET ROLE;
-- Revoke employee consent in the DB, then prove that the service-role RPCs
-- do not permit another commit or even a secret cursor read.
UPDATE public.lola_channel_authorizations SET
  employee_consent_revoked_at=clock_timestamp()
WHERE company_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  AND employee_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
  AND channel='email';
SET ROLE service_role;
DO $revocation$
DECLARE
  mapping text;
  result jsonb;
  company uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  employee uuid := 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
BEGIN
  SELECT encode(extensions.digest(
    convert_to(concat_ws(E'\n',
      company::text,employee::text,
      'cccccccc-cccc-4ccc-8ccc-cccccccccccc','verified-case-1',
      'link-source-rev-1',
      'email:auth-email-1','whatsapp_business:auth-wa-1'
    ),'UTF8'),'sha256'),'hex') INTO mapping;
  SELECT public.lola_delta_checkpoint_commit(
    company,employee,'outlook',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    'employee-verified-outlook','mailbox-1','inbox',mapping,
    2,repeat('d',64),repeat('T',64),repeat('f',64),repeat('e',64),
    '[]'::jsonb,clock_timestamp()-interval '10 seconds'
  ) INTO result;
  IF result<> '{"committed":false}'::jsonb THEN
    RAISE EXCEPTION 'Revoked employee consent did not block persistence';
  END IF;
  BEGIN
    PERFORM public.lola_delta_checkpoint_read(
      company,employee,'outlook',
      'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      'employee-verified-outlook','mailbox-1','inbox',mapping
    );
    RAISE EXCEPTION 'Revoked employee was able to read provider cursor';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
END
$revocation$;
RESET ROLE;

DO $postflight$
BEGIN
  IF (SELECT count(*) FROM public.lola_delta_checkpoints)<>1
     OR (SELECT version FROM public.lola_delta_checkpoints)<>2
     OR (SELECT count(*) FROM public.lola_delta_event_refs)<>3
  THEN
    RAISE EXCEPTION 'Revocation or replay changed the durable transaction unexpectedly';
  END IF;
END
$postflight$;
