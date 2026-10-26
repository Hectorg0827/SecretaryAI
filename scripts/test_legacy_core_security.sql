-- Disposable PostgreSQL CI: legacy core schema has tenant read-only client access.
-- Supabase migrations for these objects must not add anonymous or client write grants.
DO $checks$
DECLARE
  t text;
  arr text[] := ARRAY['report_snapshots','account_memory','conversation_summaries',
                      'feed_events','workflow_definitions','workflow_runs'];
BEGIN
  FOREACH t IN ARRAY arr LOOP
    IF to_regclass('public.' || t) IS NULL THEN
      RAISE EXCEPTION 'Missing legacy core table: %', t;
    END IF;
    IF NOT (SELECT c.relrowsecurity FROM pg_class c
            WHERE c.oid=to_regclass('public.' || t)) THEN
      RAISE EXCEPTION 'RLS disabled for legacy table %', t;
    END IF;
    IF has_table_privilege('anon', 'public.' || t, 'SELECT') OR
       has_table_privilege('anon', 'public.' || t, 'INSERT') OR
       has_table_privilege('authenticated', 'public.' || t, 'INSERT') OR
       has_table_privilege('authenticated', 'public.' || t, 'UPDATE') OR
       has_table_privilege('authenticated', 'public.' || t, 'DELETE') THEN
      RAISE EXCEPTION 'Legacy table unexpectedly client writable/public: %', t;
    END IF;
    IF NOT has_table_privilege('authenticated', 'public.' || t, 'SELECT') OR
       NOT has_table_privilege('service_role', 'public.' || t, 'INSERT') THEN
      RAISE EXCEPTION 'Expected read/backend grants missing on %', t;
    END IF;
    IF NOT EXISTS(
      SELECT 1 FROM pg_policy
      WHERE polrelid=to_regclass('public.' || t)
        AND polcmd='r'
        AND polqual IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'Tenant SELECT policy missing from %', t;
    END IF;
  END LOOP;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns WHERE table_schema='public'
      AND table_name='drafts' AND column_name='expires_at'
      AND is_generated='NEVER'
  ) THEN
    RAISE EXCEPTION 'Draft expiry must not use a generated timestamptz column';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns WHERE table_schema='public'
      AND table_name='ingested_files' AND column_name='file_hash'
  ) THEN
    RAISE EXCEPTION 'Ingested file deduplication column missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE schemaname='public'
      AND tablename='ingested_files' AND indexname='idx_ingested_files_hash'
  ) THEN
    RAISE EXCEPTION 'Ingested file deduplication index missing';
  END IF;
END $checks$;
