-- SECURITY NEGATIVE TEST (disposable PostgreSQL only).
-- Demonstrates a release-blocking parent-cascade bypass AFTER the child table
-- has had its direct DELETE permission removed. This must NEVER be run on
-- staging or production; uses only synthetic fixtures and ROLLBACK.
--
-- Expected result today: EXPOSURE confirmed, not a security PASS.
-- A green CI step here means we reproduced the issue, NOT that it is fixed.
\set ON_ERROR_STOP on

BEGIN;
-- Match the observed live staging parent grant shape within rollback only.
-- Grant is explicit so this fixture does not rely on synthetic database
-- initialization granting the same privileges as Supabase.
GRANT SELECT, DELETE ON TABLE public.companies TO service_role;

DO $requirements$
BEGIN
  IF has_table_privilege('service_role', 'public.logistics_events', 'DELETE') THEN
    RAISE EXCEPTION 'Precondition missing: child direct DELETE still granted';
  END IF;
  IF NOT (SELECT rolbypassrls FROM pg_roles WHERE rolname = 'service_role') THEN
    RAISE EXCEPTION 'Precondition missing: service_role must bypass RLS';
  END IF;
  IF NOT has_table_privilege('service_role', 'public.companies', 'DELETE') THEN
    RAISE EXCEPTION 'Precondition missing: parent DELETE grant';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.logistics_events'::regclass
      AND confrelid = 'public.companies'::regclass
      AND confdeltype = 'c'
  ) THEN
    RAISE EXCEPTION 'Precondition missing: company FK must cascade';
  END IF;
END;
$requirements$;

INSERT INTO public.companies (id, name)
VALUES ('11111111-1111-4111-8111-111111111119', 'ci-parent-delete-bypass-only');
INSERT INTO public.logistics_events
  (event_id, company_id, shipment_id, kind, source, recorded_at, idempotency_key)
VALUES (
  'ci-parent-delete-bypass-event',
  '11111111-1111-4111-8111-111111111119',
  'ci-shipment-parent-delete-bypass',
  'milestone', 'system_calculated', '2026-11-16T00:00:00Z',
  'ci-parent-delete-bypass-unique-key'
);

-- This is the security problem: a normal backend service-role credential
-- bypasses companies RLS, so DELETE on companies cascades through the ledger,
-- even when the same role cannot DELETE directly from logistics_events.
SET LOCAL ROLE service_role;
DELETE FROM public.companies
WHERE id = '11111111-1111-4111-8111-111111111119';
RESET ROLE;

DO $exposure$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.companies
    WHERE id = '11111111-1111-4111-8111-111111111119'
  ) THEN
    RAISE EXCEPTION 'Parent company was not deleted: bypass test did not reproduce';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.logistics_events
    WHERE event_id = 'ci-parent-delete-bypass-event'
  ) THEN
    RAISE EXCEPTION 'Cascade did not remove the ledger: bypass test did not reproduce';
  END IF;
END;
$exposure$;

ROLLBACK;
SELECT 'EXPOSURE logistics_ledger_erasure_via_service_role_company_delete (release NO-GO)' AS finding;
