-- Security and tenancy acceptance for internal browser/ingestion/CU queues.
DO $internal_queues$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['browser_automation_jobs','ingestion_queue','computer_use_jobs'] LOOP
    IF to_regclass('public.' || t) IS NULL THEN
       RAISE EXCEPTION 'Missing internal job queue %', t;
    END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('public.' || t)) THEN
       RAISE EXCEPTION 'RLS must be enabled on internal job queue %', t;
    END IF;
    IF has_table_privilege('anon','public.' || t,'SELECT')
       OR has_table_privilege('authenticated','public.' || t,'SELECT')
       OR has_table_privilege('authenticated','public.' || t,'INSERT')
       OR has_table_privilege('authenticated','public.' || t,'UPDATE')
       OR has_table_privilege('authenticated','public.' || t,'DELETE') THEN
       RAISE EXCEPTION 'Internal job queue accessible to client role: %', t;
    END IF;
    IF NOT has_table_privilege('service_role','public.' || t,'SELECT')
       OR NOT has_table_privilege('service_role','public.' || t,'INSERT')
       OR NOT has_table_privilege('service_role','public.' || t,'UPDATE') THEN
       RAISE EXCEPTION 'Worker role permissions absent on %', t;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint co
      WHERE co.conrelid=to_regclass('public.' || t) AND co.contype='f'
        AND co.confrelid='public.companies'::regclass
    ) THEN
       RAISE EXCEPTION 'Tenant FK missing on job queue %', t;
    END IF;
  END LOOP;
END $internal_queues$;
