-- REVIEW-ONLY / DISPOSABLE POSTGRES TEST PROPOSAL.
-- NOT A MIGRATION. DO NOT RUN ON THE ACTIVE STAGING OR PRODUCTION PROJECT.
--
-- This denies ordinary application roles the parent-company delete route
-- that currently cascades through Logistics, audit and many tenant tables.
-- Apply only AFTER dedicated registrar onboarding and independent lawful
-- erasure administration have been approved and accepted.
--
-- Critically, this does NOT constrain the postgres/table OWNER identity or
-- an application runtime holding a broadly privileged DATABASE_URL. A clean
-- release requires removing that identity from all ordinary API processes.
BEGIN;

-- Explicitly block both parent-row deletion and destructive table truncation.
REVOKE DELETE, TRUNCATE ON TABLE public.companies
  FROM PUBLIC, anon, authenticated, service_role;

-- Defense in depth on the append-only Logistics ledger. Direct UPDATE and
-- DELETE must also be refused, independent of parent foreign-key cascades.
REVOKE DELETE, UPDATE, TRUNCATE ON TABLE public.logistics_events
  FROM PUBLIC, anon, authenticated, service_role;

DO $assert$
DECLARE
  r text;
  op text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role','secretary_registrar'] LOOP
    FOREACH op IN ARRAY ARRAY['DELETE','TRUNCATE'] LOOP
      IF has_table_privilege(r, 'public.companies', op) THEN
        RAISE EXCEPTION 'FORBIDDEN company privilege remains: % %', r, op;
      END IF;
    END LOOP;
    FOREACH op IN ARRAY ARRAY['DELETE','UPDATE','TRUNCATE'] LOOP
      IF has_table_privilege(r, 'public.logistics_events', op) THEN
        RAISE EXCEPTION 'FORBIDDEN Logistics privilege remains: % %', r, op;
      END IF;
    END LOOP;
  END LOOP;
  IF NOT has_table_privilege('service_role', 'public.logistics_events', 'SELECT')
     OR NOT has_table_privilege('service_role', 'public.logistics_events', 'INSERT')
  THEN
    RAISE EXCEPTION 'Regression: normal Logistics append/read disabled';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid='public.logistics_events'::regclass
       AND confrelid='public.companies'::regclass
       AND confdeltype='c'
  ) THEN
    RAISE EXCEPTION 'Unexpected FK lifecycle change';
  END IF;
END;
$assert$;

COMMIT;
