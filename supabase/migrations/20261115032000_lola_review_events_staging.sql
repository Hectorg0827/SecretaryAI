-- Lola Stage 3 — additive, staging-only operator review journal.
-- DO NOT apply to a hosted project without explicit deployment approval.
-- Human acknowledgement is not action approval or permission to execute.
CREATE TABLE IF NOT EXISTS public.lola_review_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL CHECK (event_key ~ '^[0-9a-f]{64}$'),
    source_kind TEXT NOT NULL DEFAULT 'logistics_alert'
        CHECK (source_kind = 'logistics_alert'),
    source_alert_id TEXT NOT NULL
        CHECK (length(btrim(source_alert_id)) BETWEEN 1 AND 160),
    source_revision TEXT NOT NULL CHECK (source_revision ~ '^[0-9a-f]{64}$'),
    shipment_ref TEXT NOT NULL CHECK (length(btrim(shipment_ref)) BETWEEN 1 AND 160),
    alert_code TEXT NOT NULL CHECK (alert_code ~ '^[A-Z0-9_]{2,80}$'),
    severity TEXT NOT NULL CHECK (severity IN ('critical','alert','warning','info')),
    evidence_quality TEXT NOT NULL CHECK (
        evidence_quality IN ('monitor_derived','unverified_report_requires_confirmation')
    ),
    review_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_state IN ('pending','acknowledged')),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by UUID REFERENCES public.users(id) ON DELETE RESTRICT,
    CONSTRAINT lola_review_events_company_key_unique UNIQUE (company_id,event_key),
    CONSTRAINT lola_review_events_review_integrity CHECK (
        (review_state='pending' AND acknowledged_at IS NULL AND acknowledged_by IS NULL)
        OR (review_state='acknowledged' AND acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS lola_review_events_pending_idx
  ON public.lola_review_events (company_id,review_state,first_seen_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS lola_review_events_shipment_idx
  ON public.lola_review_events (company_id,shipment_ref,first_seen_at DESC);

ALTER TABLE public.lola_review_events ENABLE ROW LEVEL SECURITY;
-- No browser-user policies. The app's authenticated, active-role-checked
-- endpoints mediate access. The server's service-role bypasses RLS; every
-- backend read/write MUST explicitly filter by company_id and exact key.
REVOKE ALL ON public.lola_review_events FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.lola_review_events TO service_role;
-- No DELETE, no table-level execution privileges and no SECURITY DEFINER RPCs.
