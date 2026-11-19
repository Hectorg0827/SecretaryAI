-- Workstream 3 — Logistics journey ledger (append-only event log)
-- Required by: backend/app/intelligence/wholesale_distribution/logistics/store.py

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
    CONSTRAINT ck_logistics_events_confirmer CHECK (
        source NOT IN ('user_confirmed','document_validated') OR confirmed_by <> ''
    )
);

CREATE INDEX IF NOT EXISTS idx_logistics_events_company_recorded
    ON logistics_events (company_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_logistics_events_shipment
    ON logistics_events (company_id, shipment_id);

ALTER TABLE logistics_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON logistics_events FROM PUBLIC, anon, authenticated;
REVOKE ALL ON logistics_events FROM service_role;
GRANT SELECT, INSERT, DELETE ON logistics_events TO service_role;

CREATE OR REPLACE FUNCTION logistics_events_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'logistics_events is append-only (UPDATE not allowed)';
END;
$$;

DROP TRIGGER IF EXISTS trg_logistics_events_append_only ON logistics_events;
CREATE TRIGGER trg_logistics_events_append_only
    BEFORE UPDATE ON logistics_events
    FOR EACH ROW EXECUTE FUNCTION logistics_events_append_only();
