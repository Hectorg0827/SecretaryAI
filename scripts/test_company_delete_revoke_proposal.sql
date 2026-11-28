-- REVIEW-ONLY MITIGATION SPIKE: disposable PostgreSQL only.
-- This tests a possible least-privilege posture AFTER first proving the
-- real service_role parent-cascade exposure in the preceding negative test.
-- It is NOT a migration, and it MUST NOT be run on staging or production.
--
-- WARNING: backend/app/api/auth.py currently relies on service_role DELETE
-- for orphan registration cleanup. Deploying this grant revocation without
-- first refactoring registration would break current application behavior.
\set ON_ERROR_STOP on
BEGIN;

-- Reconstruct the observed live staging parent-table grant for the fixture.
GRANT SELECT, DELETE ON TABLE public.companies TO service_role;

-- Illustrative least-privilege change, undone by ROLLBACK.
REVOKE DELETE ON TABLE public.companies
  FROM PUBLIC, anon, authenticated, service_role;

DO $grantcheck$
BEGIN
  IF has_table_privilege('service_role','public.companies','DELETE') OR
     has_table_privilege('authenticated','public.companies','DELETE') OR
     has_table_privilege('anon','public.companies','DELETE')
  THEN
    RAISE EXCEPTION 'Illustrative company DELETE revocation incomplete';
  END IF;
  IF NOT has_table_privilege('service_role','public.logistics_events','SELECT')
     OR NOT has_table_privilege('service_role','public.logistics_events','INSERT')
  THEN
    RAISE EXCEPTION 'Legitimate Logistics app read/append permissions changed';
  END IF;
END;
$grantcheck$;

INSERT INTO public.companies (id,name)
VALUES ('11111111-1111-4111-8111-111111111120','ci-revoke-only-proposal');
INSERT INTO public.logistics_events
 (event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
VALUES
 ('ci-revoke-only-event',
  '11111111-1111-4111-8111-111111111120',
  'ci-proposal-shipment','milestone','system_calculated','2026-11-16T00:00:00Z',
  'ci-revoke-only-event-idempotency');

SET LOCAL ROLE service_role;
DO $denied$
DECLARE permission_denied boolean := false;
BEGIN
  BEGIN
    DELETE FROM public.companies
    WHERE id = '11111111-1111-4111-8111-111111111120';
  EXCEPTION WHEN insufficient_privilege THEN
    permission_denied := true;
  END;
  IF NOT permission_denied THEN
    RAISE EXCEPTION 'Service-role company DELETE unexpectedly succeeded';
  END IF;
END;
$denied$;
RESET ROLE;

DO $survives$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.companies
        WHERE id='11111111-1111-4111-8111-111111111120')
     OR NOT EXISTS(SELECT 1 FROM public.logistics_events
        WHERE event_id='ci-revoke-only-event')
  THEN RAISE EXCEPTION 'Company/event was lost despite expected denial'; END IF;
END;
$survives$;

-- The database owner can still execute a separately authorized cascade.
DELETE FROM public.companies
WHERE id='11111111-1111-4111-8111-111111111120';

DO $owner_cascade$
BEGIN
  IF EXISTS(SELECT 1 FROM public.logistics_events
            WHERE event_id='ci-revoke-only-event')
  THEN RAISE EXCEPTION 'Privileged owner cascade no longer works'; END IF;
END;
$owner_cascade$;

ROLLBACK;
SELECT 'REVIEW_ONLY company_delete_revoke_blocks_service_role_but_onboarding_refactor_required' AS result;
