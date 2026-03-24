CREATE TABLE IF NOT EXISTS workflow_definitions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid REFERENCES companies(id) ON DELETE CASCADE,  -- NULL = system workflow
    name         text NOT NULL,
    description  text,
    trigger_type text NOT NULL,  -- 'low_stock' | 'dormant_account' | 'overdue_invoice' | 'manual'
    steps        jsonb NOT NULL DEFAULT '[]',
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id           uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    workflow_name        text NOT NULL,
    trigger_type         text NOT NULL,
    trigger_data         jsonb NOT NULL DEFAULT '{}',
    current_step         int NOT NULL DEFAULT 0,
    total_steps          int NOT NULL DEFAULT 0,
    status               text NOT NULL DEFAULT 'running',  -- 'running' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled'
    step_results         jsonb NOT NULL DEFAULT '[]',
    started_at           timestamptz NOT NULL DEFAULT now(),
    completed_at         timestamptz,
    awaiting_entity_id   text,  -- draft ID waiting for approval
    CONSTRAINT workflow_runs_status_check CHECK (status IN ('running','awaiting_approval','completed','failed','cancelled'))
);
CREATE INDEX IF NOT EXISTS workflow_runs_company_idx ON workflow_runs (company_id, status);
CREATE INDEX IF NOT EXISTS workflow_runs_awaiting_idx ON workflow_runs (awaiting_entity_id) WHERE status = 'awaiting_approval';

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "workflow_runs_company_isolation" ON workflow_runs
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));
