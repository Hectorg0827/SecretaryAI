-- Read-only verification of SecretaryAI V1-V5 schema and later V6 stock lockdown.
-- Safe against a disposable CI database or the isolated SecretaryAI staging DB.
-- Does not write migration-history records or create tenant data.
DO $baseline$
BEGIN
  IF NOT (
    to_regclass('public.companies') IS NOT NULL
    AND to_regclass('public.users') IS NOT NULL
    AND to_regclass('public.accounts') IS NOT NULL
    AND to_regclass('public.orders') IS NOT NULL
    AND to_regclass('public.inventory') IS NOT NULL
    AND to_regclass('public.conversations') IS NOT NULL
    AND to_regclass('public.action_log') IS NOT NULL
    AND to_regclass('public.sync_checks') IS NOT NULL
    AND to_regclass('public.drafts') IS NOT NULL
    AND to_regclass('public.computer_use_sessions') IS NOT NULL
    AND to_regclass('public.computer_use_blocked_actions') IS NOT NULL
    AND to_regclass('public.ingested_files') IS NOT NULL
    AND to_regclass('public.access_router_log') IS NOT NULL
    AND to_regclass('public.stock_skus') IS NOT NULL
    AND to_regclass('public.stock_warehouses') IS NOT NULL
    AND to_regclass('public.stock_balances') IS NOT NULL
    AND to_regclass('public.stock_movements') IS NOT NULL
    AND to_regprocedure('public.post_stock_movement(uuid,text,uuid,text,text,text,numeric,text,text,text,uuid)') IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'V1-V5 schema baseline objects incomplete';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='companies'
      AND column_name='google_connected_at'
  ) OR NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='companies'
      AND column_name='qbo_connected_at'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='connector_install_secret_digest_format'
  ) THEN
    RAISE EXCEPTION 'V3/V4 integration columns or digest constraint missing';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relrowsecurity
  ) THEN
    RAISE EXCEPTION 'At least one public table lacks row-level security';
  END IF;

  IF has_function_privilege('anon',
      'public.post_stock_movement(uuid,text,uuid,text,text,text,numeric,text,text,text,uuid)','EXECUTE')
    OR has_function_privilege('authenticated',
      'public.post_stock_movement(uuid,text,uuid,text,text,text,numeric,text,text,text,uuid)','EXECUTE')
    OR has_function_privilege('service_role',
      'public.post_stock_movement(uuid,text,uuid,text,text,text,numeric,text,text,text,uuid)','EXECUTE')
  THEN
    RAISE EXCEPTION 'Direct stock posting is still exposed';
  END IF;
END $baseline$;
