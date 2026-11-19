-- Local-only assertions; no tenant or OAuth records are accessed.
DO $replay$
DECLARE ntables integer; nfuncs integer; ntriggers integer; npolicies integer; nmigrations integer;
BEGIN
  SELECT count(*) INTO ntables FROM pg_catalog.pg_tables WHERE schemaname='public';
  SELECT count(*) INTO nfuncs FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public';
  SELECT count(*) INTO ntriggers FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal;
  SELECT count(*) INTO npolicies FROM pg_catalog.pg_policies WHERE schemaname='public';
  SELECT count(*) INTO nmigrations FROM supabase_migrations.schema_migrations;
  IF ntables<>78 OR nfuncs<>25 OR ntriggers<>14 OR npolicies<>43 OR nmigrations<>46 THEN
    RAISE EXCEPTION 'Replay catalog drift: tables %, functions %, triggers %, policies %, migrations %',ntables,nfuncs,ntriggers,npolicies,nmigrations;
  END IF;
  IF to_regclass('public.companies') IS NULL OR to_regclass('public.users') IS NULL
       OR to_regclass('public.purchasing_orders') IS NULL OR to_regclass('public.stock_movements') IS NULL THEN
    RAISE EXCEPTION 'Replay is missing required core tables';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='companies' AND column_name='qbo_token_refreshed_at') THEN
    RAISE EXCEPTION 'Replay is missing QBO token-refresh column';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies WHERE schemaname='public' AND tablename='conversations'
       AND policyname='conversation_owner_scope' AND qual LIKE '%user_id%' AND qual LIKE '%uid%' AND with_check IS NOT NULL) THEN
    RAISE EXCEPTION 'Replay is missing owner-scoped conversations policy';
  END IF;
  RAISE NOTICE 'PASS: 46 canonical versions; public schema and core safety objects match expected baseline';
END $replay$;

-- Exact public-schema structural fingerprints from read-only shared-main comparison.
-- Grants are intentionally not compared because isolated preview is least-privilege.
DO $catalog_parity$
DECLARE actual text;
BEGIN
 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT table_name||'.'||column_name||':'||ordinal_position::text||':'||data_type||':'||is_nullable||':'||coalesce(column_default,'') AS d FROM information_schema.columns WHERE table_schema='public') x;
 IF actual<>'fdf6e1eeb1cc2165a79441c1d40a19be' THEN RAISE EXCEPTION 'Column catalog differs: %',actual; END IF;

 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT pg_get_constraintdef(co.oid,true)||':'||co.conname||':'||c.relname AS d FROM pg_catalog.pg_constraint co JOIN pg_catalog.pg_class c ON c.oid=co.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') x;
 IF actual<>'5ed30a132b367ae4b46f9a04f7be4b0d' THEN RAISE EXCEPTION 'Constraint catalog differs: %',actual; END IF;

 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT pg_get_indexdef(i.indexrelid)||':'||c.relname AS d FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') x;
 IF actual<>'156240329b43bcb837babc630810be74' THEN RAISE EXCEPTION 'Index catalog differs: %',actual; END IF;

 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT p.proname||':'||pg_get_function_identity_arguments(p.oid)||':'||pg_get_functiondef(p.oid) AS d FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public') x;
 IF actual<>'b7f9434e696df921fa4cfc0788c66f04' THEN RAISE EXCEPTION 'Function catalog differs: %',actual; END IF;

 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT c.relname||':'||t.tgname||':'||pg_get_triggerdef(t.oid,true) AS d FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal) x;
 IF actual<>'a73156c43d0267dec19a9108cce1ed0c' THEN RAISE EXCEPTION 'Trigger catalog differs: %',actual; END IF;

 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT tablename||':'||policyname||':'||cmd||':'||coalesce(qual,'')||':'||coalesce(with_check,'')||':'||coalesce(array_to_string(roles,','),'') AS d FROM pg_catalog.pg_policies WHERE schemaname='public') x;
 IF actual<>'fbe8b89b7f93c92a6f2cd61330ebe825' THEN RAISE EXCEPTION 'RLS policies differ: %',actual; END IF;

 SELECT md5(coalesce(string_agg(d,'|' ORDER BY d),''))
 INTO actual FROM (SELECT c.relname||':'||c.relrowsecurity::text||':'||c.relforcerowsecurity::text AS d FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')) x;
 IF actual<>'0d176a51cab245614c4e2a1d2a1d8901' THEN RAISE EXCEPTION 'RLS flags differ: %',actual; END IF;
 RAISE NOTICE 'PASS: all seven public-schema catalog fingerprints equal shared main';
END $catalog_parity$;
