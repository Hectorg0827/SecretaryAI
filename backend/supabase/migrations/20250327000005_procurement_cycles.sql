-- Migration: procurement cycles tracking table
-- Required by: backend/app/api/logistics.py GET /pipeline/{po_number}

CREATE TABLE IF NOT EXISTS procurement_cycles (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    po_number       text        NOT NULL,
    current_stage   int         NOT NULL DEFAULT 1 CHECK (current_stage BETWEEN 1 AND 9),
    stage_name      text,
    supplier_id     text,
    supplier_name   text,
    total_cases     int,
    total_value     numeric(12, 2),
    notes           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_procurement_cycles_company_po UNIQUE (company_id, po_number)
);

-- RLS
ALTER TABLE procurement_cycles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON procurement_cycles
    USING (company_id = (current_setting('app.company_id', true))::uuid);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_procurement_cycles_company
    ON procurement_cycles(company_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_procurement_cycles_po
    ON procurement_cycles(company_id, po_number);

-- auto-update updated_at
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON procurement_cycles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
