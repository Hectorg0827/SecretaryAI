-- Synthetic tenant isolation test, scoped to local-only Supabase Postgres.
-- All test rows and temporary GRANTs are rolled back. NOT an HTTP/signed-JWT test.
-- Capture privileges before the temporary GRANT; local Supabase defaults can be
-- broader than least-privilege hosted preview without implying new exposure.
CREATE TEMP TABLE finance_rls_initial_acl AS
SELECT pg_catalog.has_table_privilege('authenticated','public.conversations','SELECT') AS conversations_select,
       pg_catalog.has_table_privilege('authenticated','public.accounts','SELECT') AS accounts_select,
       pg_catalog.has_table_privilege('authenticated','public.users','SELECT') AS users_select;
BEGIN;
INSERT INTO public.companies(id,name) VALUES
('11111111-1111-4111-8111-111111111110','__rls_tenant_A'),
('22222222-2222-4222-8222-222222222220','__rls_tenant_B');
INSERT INTO public.users(id,company_id,email,hashed_password,role) VALUES
('10000000-0000-4000-8000-000000000001','11111111-1111-4111-8111-111111111110','rls-A1@example.invalid','invalid-hash','owner'),
('10000000-0000-4000-8000-000000000002','11111111-1111-4111-8111-111111111110','rls-A2@example.invalid','invalid-hash','viewer'),
('10000000-0000-4000-8000-000000000003','22222222-2222-4222-8222-222222222220','rls-B1@example.invalid','invalid-hash','owner');
INSERT INTO public.accounts(id,company_id,name) VALUES
('30000000-0000-4000-8000-000000000001','11111111-1111-4111-8111-111111111110','__rls_account_A'),
('30000000-0000-4000-8000-000000000002','22222222-2222-4222-8222-222222222220','__rls_account_B');
INSERT INTO public.conversations(id,company_id,user_id,title) VALUES
('20000000-0000-4000-8000-000000000001','11111111-1111-4111-8111-111111111110','10000000-0000-4000-8000-000000000001','__rls_conv_A1'),
('20000000-0000-4000-8000-000000000002','11111111-1111-4111-8111-111111111110','10000000-0000-4000-8000-000000000002','__rls_conv_A2'),
('20000000-0000-4000-8000-000000000003','22222222-2222-4222-8222-222222222220','10000000-0000-4000-8000-000000000003','__rls_conv_B1');
GRANT SELECT,INSERT,UPDATE,DELETE ON public.conversations,public.accounts,public.users TO authenticated;
SET LOCAL ROLE authenticated;
SELECT pg_catalog.set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000001","company_id":"11111111-1111-4111-8111-111111111110","role":"authenticated"}',true);
DO $a1$
DECLARE c int; a int; u int; changed int;
BEGIN
 SELECT count(*) INTO c FROM public.conversations;
 SELECT count(*) INTO a FROM public.accounts;
 SELECT count(*) INTO u FROM public.users;
 IF c<>1 OR a<>1 OR u<>2 THEN RAISE EXCEPTION 'A1 tenant leak: %, %, %',c,a,u; END IF;
 UPDATE public.conversations SET title='__forbidden' WHERE id='20000000-0000-4000-8000-000000000003';
 GET DIAGNOSTICS changed=ROW_COUNT;
 IF changed<>0 THEN RAISE EXCEPTION 'Cross-tenant UPDATE changed % rows',changed; END IF;
 BEGIN
  INSERT INTO public.conversations(id,company_id,user_id,title) VALUES
  ('20000000-0000-4000-8000-000000000004','22222222-2222-4222-8222-222222222220','10000000-0000-4000-8000-000000000003','__forbidden');
  RAISE EXCEPTION 'Cross-tenant INSERT was allowed';
 EXCEPTION WHEN insufficient_privilege THEN NULL;
 END;
END $a1$;
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT pg_catalog.set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000002","company_id":"11111111-1111-4111-8111-111111111110","role":"authenticated"}',true);
DO $a2$
BEGIN
 IF (SELECT count(*) FROM public.conversations)<>1 OR
 EXISTS(SELECT 1 FROM public.conversations WHERE id='20000000-0000-4000-8000-000000000001') THEN
 RAISE EXCEPTION 'Same-tenant conversation owner leakage';
 END IF;
END $a2$;
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT pg_catalog.set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000003","company_id":"22222222-2222-4222-8222-222222222220","role":"authenticated"}',true);
DO $b1$
BEGIN
 IF (SELECT count(*) FROM public.conversations)<>1 OR
 (SELECT count(*) FROM public.accounts)<>1 OR
 (SELECT count(*) FROM public.users)<>1 THEN
 RAISE EXCEPTION 'Tenant B isolation failed';
 END IF;
END $b1$;
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT pg_catalog.set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated"}',true);
DO $noclaim$
BEGIN
 IF (SELECT count(*) FROM public.conversations)<>0 OR
 (SELECT count(*) FROM public.accounts)<>0 OR
 (SELECT count(*) FROM public.users)<>0 THEN
 RAISE EXCEPTION 'Missing tenant claim did not fail closed';
 END IF;
END $noclaim$;
RESET ROLE;
ROLLBACK;
-- Separate post-rollback statement verifies complete cleanup.
DO $cleanup$
BEGIN
 IF (SELECT count(*) FROM public.companies)<>0 OR
 (SELECT count(*) FROM public.users)<>0 OR
 (SELECT count(*) FROM public.accounts)<>0 OR
 (SELECT count(*) FROM public.conversations)<>0 OR
 pg_catalog.has_table_privilege('authenticated','public.conversations','SELECT') IS DISTINCT FROM (SELECT conversations_select FROM finance_rls_initial_acl) OR
 pg_catalog.has_table_privilege('authenticated','public.accounts','SELECT') IS DISTINCT FROM (SELECT accounts_select FROM finance_rls_initial_acl) OR
 pg_catalog.has_table_privilege('authenticated','public.users','SELECT') IS DISTINCT FROM (SELECT users_select FROM finance_rls_initial_acl) THEN
 RAISE EXCEPTION 'Synthetic RLS test failed to roll back rows or grants';
 END IF;
 RAISE NOTICE 'PASS: two tenants, three employees, cross-tenant read/write denial, owner scope, missing claims, rollback';
END $cleanup$;
DROP TABLE finance_rls_initial_acl;

-- Rolled-back verification of post-replay TRUNCATE revocation and GoTrue Custom Access Token Hook
-- (validates Issue #215 and PR #232/#233 auth claim safety on live Supabase PostgreSQL 17).
BEGIN;
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM PUBLIC, anon, authenticated;
CREATE OR REPLACE FUNCTION secretary_internal.custom_access_token_hook(event jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $auth_hook$
DECLARE
  claims jsonb := coalesce(event -> 'claims', '{}'::jsonb) - 'company_id';
  raw_company text := event #>> '{claims,app_metadata,company_id}';
  raw_user text := coalesce(event ->> 'user_id', event #>> '{claims,sub}');
  v_company_id uuid;
  v_user_id uuid;
BEGIN
  IF raw_company IS NULL
     OR raw_company !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
    RETURN jsonb_set(event, '{claims}', claims, true);
  END IF;
  v_company_id := raw_company::uuid;
  IF NOT EXISTS (SELECT 1 FROM public.companies c WHERE c.id = v_company_id) THEN
    RETURN jsonb_set(event, '{claims}', claims, true);
  END IF;
  IF raw_user IS NOT NULL
     AND raw_user ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
    v_user_id := raw_user::uuid;
    IF EXISTS (
      SELECT 1 FROM public.users u
       WHERE u.id = v_user_id
         AND (u.is_active IS DISTINCT FROM TRUE OR u.company_id IS DISTINCT FROM v_company_id)
    ) THEN
      RETURN jsonb_set(event, '{claims}', claims, true);
    END IF;
  END IF;
  claims := jsonb_set(claims, '{company_id}', to_jsonb(v_company_id::text), true);
  RETURN jsonb_set(event, '{claims}', claims, true);
END;
$auth_hook$;
REVOKE ALL ON FUNCTION secretary_internal.custom_access_token_hook(jsonb) FROM PUBLIC, anon, authenticated, service_role;

DO $hook_and_truncate_verify$
DECLARE
  trunc_grants integer;
  out_valid jsonb;
  out_inactive jsonb;
  out_spoofed jsonb;
BEGIN
  SELECT count(*) INTO trunc_grants
    FROM information_schema.role_table_grants
   WHERE table_schema = 'public'
     AND privilege_type = 'TRUNCATE'
     AND grantee IN ('PUBLIC', 'anon', 'authenticated');
  IF trunc_grants <> 0 THEN
    RAISE EXCEPTION 'Expected 0 client TRUNCATE grants after revocation, found %', trunc_grants;
  END IF;

  INSERT INTO public.companies(id, name) VALUES ('11111111-1111-4111-8111-111111111110', '__hook_co');
  INSERT INTO public.users(id, company_id, email, hashed_password, role, is_active) VALUES
    ('10000000-0000-4000-8000-000000000009', '11111111-1111-4111-8111-111111111110', 'inactive@example.invalid', 'invalid-hash', 'viewer', false);

  out_valid := secretary_internal.custom_access_token_hook(
    '{"user_id":"10000000-0000-4000-8000-000000000001","claims":{"sub":"10000000-0000-4000-8000-000000000001","app_metadata":{"company_id":"11111111-1111-4111-8111-111111111110"}}}'::jsonb
  );
  IF out_valid #>> '{claims,company_id}' <> '11111111-1111-4111-8111-111111111110' THEN
    RAISE EXCEPTION 'Auth hook failed to propagate valid company_id: %', out_valid;
  END IF;

  out_inactive := secretary_internal.custom_access_token_hook(
    '{"user_id":"10000000-0000-4000-8000-000000000009","claims":{"sub":"10000000-0000-4000-8000-000000000009","app_metadata":{"company_id":"11111111-1111-4111-8111-111111111110"}}}'::jsonb
  );
  IF (out_inactive -> 'claims') ? 'company_id' THEN
    RAISE EXCEPTION 'Auth hook failed to withhold company_id for deactivated employee';
  END IF;

  out_spoofed := secretary_internal.custom_access_token_hook(
    '{"user_id":"10000000-0000-4000-8000-000000000001","claims":{"sub":"10000000-0000-4000-8000-000000000001","company_id":"11111111-1111-4111-8111-111111111110","user_metadata":{"company_id":"11111111-1111-4111-8111-111111111110"}}}'::jsonb
  );
  IF (out_spoofed -> 'claims') ? 'company_id' THEN
    RAISE EXCEPTION 'Auth hook failed to strip unverified/user_metadata company_id';
  END IF;
END $hook_and_truncate_verify$;
ROLLBACK;

DO $post_hook_cleanup$
BEGIN
  IF (SELECT count(*) FROM public.companies) <> 0 OR (SELECT count(*) FROM public.users) <> 0 THEN
    RAISE EXCEPTION 'Hook verification transaction failed to roll back rows';
  END IF;
  RAISE NOTICE 'PASS: client TRUNCATE revocation and custom_access_token_hook verified and rolled back';
END $post_hook_cleanup$;
