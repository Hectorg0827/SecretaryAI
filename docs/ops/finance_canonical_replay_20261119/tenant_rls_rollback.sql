-- Synthetic tenant isolation test, scoped to local-only Supabase Postgres.
-- All test rows and temporary GRANTs are rolled back. NOT an HTTP/signed-JWT test.
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
 pg_catalog.has_table_privilege('authenticated','public.conversations','SELECT') OR
 pg_catalog.has_table_privilege('authenticated','public.accounts','SELECT') OR
 pg_catalog.has_table_privilege('authenticated','public.users','SELECT') THEN
 RAISE EXCEPTION 'Synthetic RLS test failed to roll back rows or grants';
 END IF;
 RAISE NOTICE 'PASS: two tenants, three employees, cross-tenant read/write denial, owner scope, missing claims, rollback';
END $cleanup$;
