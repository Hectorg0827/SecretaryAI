-- Fail-closed acceptance for Phase-1 backend control-plane tables.
DO $phase1$
DECLARE
  t text;
  priv text[] := ARRAY['connector_registrations','connector_sync_logs',
                       'automation_sessions','policy_rules','ingestion_jobs',
                       'browser_jobs','audit_events','device_push_tokens'];
BEGIN
  FOREACH t IN ARRAY priv LOOP
    IF to_regclass('public.'||t) IS NULL OR NOT (
      SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('public.'||t)
    ) THEN
      RAISE EXCEPTION 'Missing RLS-enabled control-plane table %',t;
    END IF;
    IF has_table_privilege('anon','public.'||t,'SELECT')
       OR has_table_privilege('authenticated','public.'||t,'SELECT')
       OR has_table_privilege('authenticated','public.'||t,'INSERT')
       OR has_table_privilege('authenticated','public.'||t,'UPDATE')
       OR has_table_privilege('authenticated','public.'||t,'DELETE') THEN
      RAISE EXCEPTION 'Client control-plane privilege leak on %',t;
    END IF;
    IF NOT has_table_privilege('service_role','public.'||t,'SELECT')
       OR NOT has_table_privilege('service_role','public.'||t,'UPDATE') THEN
      RAISE EXCEPTION 'Service-role control-plane access absent on %',t;
    END IF;
  END LOOP;
  IF to_regclass('public.company_features') IS NULL OR NOT (
    SELECT relrowsecurity FROM pg_class WHERE oid='public.company_features'::regclass
  ) THEN
     RAISE EXCEPTION 'Company feature table RLS missing';
  END IF;
  IF NOT has_table_privilege('authenticated','public.company_features','SELECT')
     OR has_table_privilege('authenticated','public.company_features','UPDATE')
     OR has_table_privilege('anon','public.company_features','SELECT') THEN
    RAISE EXCEPTION 'Incorrect company feature client grants';
  END IF;
  IF NOT EXISTS (
     SELECT 1 FROM pg_policy
     WHERE polrelid='public.company_features'::regclass AND polcmd='r'
       AND pg_get_expr(polqual, polrelid) LIKE '%company_id%'
  ) THEN
    RAISE EXCEPTION 'Tenant feature SELECT policy absent';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_policy
    WHERE polrelid='public.report_snapshots'::regclass
      AND polname='company_isolation'
  ) THEN
    RAISE EXCEPTION 'Phase-1 must not replace report snapshot policy';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='company_features'
      AND column_name='capability_routes'
  ) OR NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='workflow_runs'
      AND column_name='policy_rule_id'
  ) THEN
     RAISE EXCEPTION 'Phase 4 extensions absent';
  END IF;
END $phase1$;
