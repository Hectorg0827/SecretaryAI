-- Disposable PostgreSQL security acceptance, not real staging data.
-- Test 1: no direct table DELETE/UPDATE/TRUNCATE for service_role or clients.
DO $checks$
DECLARE role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY['service_role','authenticated','anon']
  LOOP
    IF has_table_privilege(role_name, 'public.logistics_events', 'DELETE')
       OR has_table_privilege(role_name, 'public.logistics_events', 'UPDATE')
       OR has_table_privilege(role_name, 'public.logistics_events', 'TRUNCATE')
    THEN
      RAISE EXCEPTION 'Historical logistics events are mutable by role %',role_name;
    END IF;
  END LOOP;
  IF NOT has_table_privilege('service_role','public.logistics_events','INSERT')
     OR NOT has_table_privilege('service_role','public.logistics_events','SELECT')
  THEN
    RAISE EXCEPTION 'Legitimate service role read/append rights were lost';
  END IF;
  IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid='public.logistics_events'::regclass)
  THEN
    RAISE EXCEPTION 'Event ledger row-level security must remain enabled';
  END IF;
END
$checks$;

-- Test 2: exercise direct DELETE rejection and company deletion cascade using
-- disposable fixture rows inside a rolled-back transaction (no lasting data).
BEGIN;
INSERT INTO public.companies (id,name)
VALUES ('11111111-1111-4111-8111-111111111114','ci-delete-guard-fixture');
INSERT INTO public.logistics_events
 (event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
VALUES
 ('ci-ledger-event-delete-guard-20261114',
  '11111111-1111-4111-8111-111111111114',
  'ci-shipment','milestone','system_calculated','2026-11-14T00:00:00Z',
  'ci-ledger-idempotency-delete-guard-20261114');

SET LOCAL ROLE service_role;
DO $denied$
BEGIN
  BEGIN
    DELETE FROM public.logistics_events
    WHERE event_id='ci-ledger-event-delete-guard-20261114';
    RAISE EXCEPTION 'service_role unexpectedly deleted historical event';
  EXCEPTION
    WHEN insufficient_privilege THEN NULL;
  END;
END
$denied$;
RESET ROLE;

DO $unchanged$
BEGIN
  IF (SELECT COUNT(*) FROM public.logistics_events
      WHERE event_id='ci-ledger-event-delete-guard-20261114')<>1
  THEN
    RAISE EXCEPTION 'Direct deletion unexpectedly changed the event log';
  END IF;
END
$unchanged$;

-- Company FK cascade must still erase dependent events for approved erasure.
DELETE FROM public.companies
WHERE id='11111111-1111-4111-8111-111111111114';

DO $cascade$
BEGIN
  IF EXISTS (SELECT 1 FROM public.logistics_events
             WHERE event_id='ci-ledger-event-delete-guard-20261114')
  THEN
    RAISE EXCEPTION 'Company deletion cascade failed to erase event';
  END IF;
END
$cascade$;
ROLLBACK;

SELECT 'PASS logistics_event_delete_privileges_and_company_cascade' AS result;
