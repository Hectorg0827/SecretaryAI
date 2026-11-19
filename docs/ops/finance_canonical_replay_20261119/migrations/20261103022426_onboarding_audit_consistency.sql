-- Bring the append-only audit writer's optional actor and context fields
-- into line with the canonical phase-one audit_events table.
-- No row deletion and no RLS/grant changes. Apply only to staging until
-- separately authorized and verified for production.
BEGIN;

ALTER TABLE public.audit_events
  ADD COLUMN IF NOT EXISTS actor_id text;

ALTER TABLE public.audit_events
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_audit_events_company_actor
  ON public.audit_events (company_id, actor_id, occurred_at DESC)
  WHERE actor_id IS NOT NULL;

COMMIT;
