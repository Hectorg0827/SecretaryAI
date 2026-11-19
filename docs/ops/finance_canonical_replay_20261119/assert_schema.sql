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
