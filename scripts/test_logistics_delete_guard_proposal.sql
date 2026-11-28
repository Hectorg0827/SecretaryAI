-- Disposable PostgreSQL ONLY. Never run against staging or production.
-- Reconstruct the exact concerning privilege/trigger shape of the recorded
-- 20261111005641 migration, then test the REVIEW-ONLY mitigation in rollback.
\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE public.logistics_events (
  event_id text PRIMARY KEY,
  company_id uuid NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE public.logistics_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.logistics_events FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, DELETE ON public.logistics_events TO service_role;

CREATE FUNCTION public.logistics_events_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $only_updates$
BEGIN
  RAISE EXCEPTION 'logistics_events UPDATE not allowed';
END;
$only_updates$;
CREATE TRIGGER trg_logistics_events_append_only
BEFORE UPDATE ON public.logistics_events
FOR EACH ROW EXECUTE FUNCTION public.logistics_events_append_only();

DO $precondition$
BEGIN
  IF NOT has_table_privilege('service_role','public.logistics_events','DELETE')
     OR EXISTS (
      SELECT 1 FROM pg_trigger
      WHERE tgrelid='public.logistics_events'::regclass AND NOT tgisinternal
      AND (tgtype & 8) = 8
     )
  THEN RAISE EXCEPTION 'Synthetic vulnerable baseline was not reproduced'; END IF;
END;
$precondition$;

\i docs/compliance/sql-proposals/logistics_events_prevent_delete_REVIEW_ONLY.sql

DO $checks$
DECLARE blocked boolean:=false;
BEGIN
  IF has_table_privilege('service_role','public.logistics_events','DELETE')
    OR has_table_privilege('authenticated','public.logistics_events','DELETE')
  THEN RAISE EXCEPTION 'Logistics event deletion is still granted'; END IF;

  IF NOT EXISTS(
    SELECT 1 FROM pg_trigger
    WHERE tgname='trg_logistics_events_prevent_delete'
      AND tgrelid='public.logistics_events'::regclass
      AND tgenabled='O'
      AND NOT tgisinternal
      AND (tgtype & 8)=8
      AND (tgtype & 2)=2
  ) THEN RAISE EXCEPTION 'Missing active BEFORE DELETE guard'; END IF;

  IF NOT EXISTS(
    SELECT 1 FROM pg_proc
    WHERE oid='public.reject_logistics_events_delete()'::regprocedure
      AND NOT prosecdef
      AND 'search_path=pg_catalog, pg_temp'=ANY(proconfig)
  ) THEN RAISE EXCEPTION 'Delete guard function not invoker-safe'; END IF;

  INSERT INTO public.logistics_events(event_id,company_id)
  VALUES('synthetic-only',gen_random_uuid());

  BEGIN
    DELETE FROM public.logistics_events WHERE event_id='synthetic-only';
  EXCEPTION WHEN insufficient_privilege THEN blocked:=true;
  END;
  IF NOT blocked THEN RAISE EXCEPTION 'Table owner bypassed immutability trigger'; END IF;

  IF NOT EXISTS(SELECT 1 FROM public.logistics_events WHERE event_id='synthetic-only')
  THEN RAISE EXCEPTION 'Append-only event was removed'; END IF;

  -- The original UPDATE guard remains active and unchanged.
  blocked:=false;
  BEGIN
    UPDATE public.logistics_events SET payload='{"modified":true}'::jsonb
    WHERE event_id='synthetic-only';
  EXCEPTION WHEN raise_exception THEN blocked:=true;
  END;
  IF NOT blocked THEN RAISE EXCEPTION 'Preexisting UPDATE immutability regressed'; END IF;
END;
$checks$;

ROLLBACK;
