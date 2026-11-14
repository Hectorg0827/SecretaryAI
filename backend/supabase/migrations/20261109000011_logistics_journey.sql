-- Workstream 3 — Logistics journey ledger (append-only event log)
-- Required by: backend/app/intelligence/wholesale_distribution/logistics/store.py
--
-- Shipment state is rebuilt by replaying these events (event sourcing), so the
-- table is append-only: the application never UPDATEs or DELETEs rows.
-- (company_id, idempotency_key) is unique so a duplicate carrier webhook,
-- re-sent EDI message or replayed feed cannot double-apply.

CREATE TABLE IF NOT EXISTS logistics_events (
    event_id         text        PRIMARY KEY,
    company_id       uuid        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    shipment_id      text        NOT NULL,
    kind             text        NOT NULL,
    code             text        NOT NULL DEFAULT '',
    source           text        NOT NULL,
    source_ref       text        NOT NULL DEFAULT '',
    occurred_at      text        NOT NULL DEFAULT '',
    recorded_at      text        NOT NULL,
    confirmed_by     text        NOT NULL DEFAULT '',
    idempotency_key  text        NOT NULL,
    payload          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    inserted_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_logistics_events_idem UNIQUE (company_id, idempotency_key),
    CONSTRAINT ck_logistics_events_source CHECK (source IN (
        'carrier_api','terminal_api','customs_system','broker_edi','forwarder_api',
        'supplier_edi','wms','erp','system_calculated','user_confirmed',
        'document_validated','email_parsed','ai_inferred','document_extracted','manual_note'
    )),
    -- Human-verified sources must name the human.
    CONSTRAINT ck_logistics_events_confirmer CHECK (
        source NOT IN ('user_confirmed','document_validated') OR confirmed_by <> ''
    )
);

CREATE INDEX IF NOT EXISTS idx_logistics_events_company_recorded
    ON logistics_events (company_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_logistics_events_shipment
    ON logistics_events (company_id, shipment_id);

ALTER TABLE logistics_events ENABLE ROW LEVEL SECURITY;

-- Only the trusted application server may read/write this full-fidelity
-- ledger. Public Data API clients MUST NOT be able to forge a machine source,
-- delete audit events, or bypass the backend's role-scoped commercial masking.
-- Supabase service_role remains server-side and bypasses RLS; every application
-- endpoint must still enforce JWT tenant and role authorization.
-- Revoke default grants, which may exist even with RLS enabled.
REVOKE ALL ON TABLE public.logistics_events FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS "company_isolation" ON public.logistics_events;
-- No anon/authenticated policies: default deny under RLS. Never grant client
-- SELECT here because raw events include commercial terms and customer IDs.

-- Append-only: block UPDATE even for clients that pass RLS. DELETE stays
-- possible so company deletion (ON DELETE CASCADE) and data-erasure requests work.
CREATE OR REPLACE FUNCTION logistics_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'logistics_events is append-only (UPDATE not allowed)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_logistics_events_append_only ON logistics_events;
CREATE TRIGGER trg_logistics_events_append_only
    BEFORE UPDATE ON logistics_events
    FOR EACH ROW EXECUTE FUNCTION logistics_events_append_only();
