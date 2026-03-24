-- Report snapshots: one cached row per (company, report_type).
-- Scheduler tasks upsert here after each run; API endpoints read from here
-- instead of re-hitting QuickBooks on every request.
--
-- report_type values:
--   'dashboard_summary'  – account health + inventory alerts + 30-day sales
--   'overnight_scan'     – full nightly account + inventory scan results
--   'inventory_alerts'   – every-6-hour inventory alert check results

CREATE TABLE IF NOT EXISTS report_snapshots (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    report_type  text        NOT NULL,
    payload      jsonb       NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    generated_by text        NOT NULL DEFAULT 'scheduler',
    UNIQUE (company_id, report_type)
);

-- Fast lookup for the API: company + type → latest snapshot
CREATE INDEX IF NOT EXISTS report_snapshots_company_type_idx
    ON report_snapshots (company_id, report_type);

-- Row-level security: users can only read snapshots for their own company
ALTER TABLE report_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "report_snapshots_company_isolation"
    ON report_snapshots
    FOR ALL
    USING (
        company_id = (
            SELECT company_id FROM users WHERE id = auth.uid()
        )
    );
