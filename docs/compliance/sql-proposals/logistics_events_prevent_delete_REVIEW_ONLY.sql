-- REVIEW-ONLY SECURITY REMEDIATION CANDIDATE.
-- THIS IS NOT AN APPLIED SUPABASE MIGRATION AND NOT AN AUTHORIZED RELEASE.
-- Generated from independent read-only inspection of SecretaryAI-Staging
-- (2026-11-16), with NO data access or migration-history mutation.
--
-- Live issue: logistics_events claims append-only, but service_role has DELETE
-- and only BEFORE UPDATE is blocked. Deleting a shipping evidence event is
-- possible using the backend's service-role credentials.
--
-- Before promoting: review retention/deletion requirements, obtain technical
-- approval, generate a NEW canonical migration with the installed CLI,
-- test it on a staging-schema clone, and separately approve staging deployment.
-- NEVER edit historical migration 20261111005641.

REVOKE DELETE ON TABLE public.logistics_events FROM service_role;

CREATE FUNCTION public.reject_logistics_events_delete()
RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $block_delete$
BEGIN
    RAISE EXCEPTION 'Logistics event ledger is append-only; DELETE is forbidden'
      USING ERRCODE = '42501';
END;
$block_delete$;

REVOKE ALL ON FUNCTION public.reject_logistics_events_delete()
    FROM PUBLIC, anon, authenticated;

CREATE TRIGGER trg_logistics_events_prevent_delete
BEFORE DELETE ON public.logistics_events
FOR EACH ROW
EXECUTE FUNCTION public.reject_logistics_events_delete();

-- Deliberately no COMMIT/BEGIN, no migration-history or existing-function edits.
