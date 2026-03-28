-- =============================================================================
-- Phase 1 Schema: Durable Connector State, Policy Engine, Ingestion Tracking,
--                 Browser Jobs, Audit Events, Feature Flags
--
-- Addresses gaps: G1 G2 G3 G8 G9 G10 G14 G15
-- =============================================================================

-- ---------------------------------------------------------------------------
-- G1 / G14 — Connector registration and sync logs
-- ---------------------------------------------------------------------------
-- Tracks each installed local connector instance (initially QB Desktop).
-- The connector calls POST /api/connectors/register on startup, then
-- sends POST /api/connectors/heartbeat every 60 s.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS connector_registrations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    connector_type  text NOT NULL,           -- 'qb_desktop' | 'qb_online' | 'google' | 'shopify'
    connector_id    text NOT NULL,           -- stable ID from the connector (e.g. hostname+install-uuid)
    version         text,                    -- connector version string (e.g. '1.2.0')
    status          text NOT NULL DEFAULT 'connected'
                        CHECK (status IN ('connected','disconnected','error','stale')),
    last_heartbeat  timestamptz,
    last_sync_at    timestamptz,
    last_sync_status text,                   -- 'ok' | 'partial' | 'error'
    last_error      text,
    capabilities    jsonb DEFAULT '[]',      -- array of capability strings this connector supports
    metadata        jsonb DEFAULT '{}',      -- arbitrary key/value from connector (OS, QB version, etc.)
    registered_at   timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_connector_registrations_company_id UNIQUE (company_id, connector_type, connector_id)
);

CREATE INDEX IF NOT EXISTS idx_connector_registrations_company
    ON connector_registrations (company_id);
CREATE INDEX IF NOT EXISTS idx_connector_registrations_status
    ON connector_registrations (status);

ALTER TABLE connector_registrations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON connector_registrations
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

COMMENT ON TABLE connector_registrations IS
    'One row per installed connector instance. Heartbeats update last_heartbeat; '
    'status becomes stale after 3× the heartbeat interval with no update.';


-- Structured per-sync log entries
CREATE TABLE IF NOT EXISTS connector_sync_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_id    uuid NOT NULL REFERENCES connector_registrations(id) ON DELETE CASCADE,
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sync_type       text NOT NULL,           -- 'full' | 'incremental' | 'on_demand'
    entity_type     text,                    -- 'customers' | 'invoices' | 'items' | 'payments'
    status          text NOT NULL            -- 'running' | 'ok' | 'partial' | 'error'
                        CHECK (status IN ('running','ok','partial','error')),
    rows_fetched    int DEFAULT 0,
    rows_upserted   int DEFAULT 0,
    rows_errored    int DEFAULT 0,
    error_detail    text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    duration_ms     int GENERATED ALWAYS AS (
        CASE WHEN completed_at IS NOT NULL
             THEN EXTRACT(MILLISECONDS FROM (completed_at - started_at))::int
        END
    ) STORED,
    metadata        jsonb DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_connector_sync_logs_connector
    ON connector_sync_logs (connector_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_sync_logs_company
    ON connector_sync_logs (company_id, started_at DESC);

ALTER TABLE connector_sync_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON connector_sync_logs
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);


-- ---------------------------------------------------------------------------
-- G2 — Durable computer-use / automation sessions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS automation_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL,
    workflow_run_id uuid REFERENCES workflow_runs(id) ON DELETE SET NULL,
    session_type    text NOT NULL            -- 'computer_use' | 'browser'
                        CHECK (session_type IN ('computer_use','browser')),
    status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','paused','awaiting_approval',
                                          'completed','failed','cancelled','timed_out')),
    target_app      text,                    -- e.g. 'quickbooks_desktop', 'customs_portal'
    objective       text,                    -- human-readable goal
    steps_completed int DEFAULT 0,
    steps_total     int,
    current_step    jsonb,                   -- current step context
    step_history    jsonb DEFAULT '[]',      -- array of completed step records
    screenshot_urls jsonb DEFAULT '[]',      -- S3/storage URLs of screenshots
    approval_token  text,                    -- set when awaiting human approval
    error_message   text,
    policy_violations jsonb DEFAULT '[]',    -- any safety rule hits during session
    -- session lifecycle
    started_at      timestamptz DEFAULT now(),
    paused_at       timestamptz,
    completed_at    timestamptz,
    timed_out_at    timestamptz,
    timeout_seconds int DEFAULT 300,
    -- correlation
    correlation_id  text,                    -- propagated request/job ID
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_automation_sessions_company
    ON automation_sessions (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_sessions_status
    ON automation_sessions (status)
    WHERE status NOT IN ('completed','cancelled','failed');

ALTER TABLE automation_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON automation_sessions
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

COMMENT ON TABLE automation_sessions IS
    'Durable state for computer-use and browser automation sessions. '
    'Sessions survive server restart. step_history provides replay capability.';


-- ---------------------------------------------------------------------------
-- G3 — Per-tenant policy rules
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS policy_rules (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    -- scope
    rule_name       text NOT NULL,
    description     text,
    -- classification this rule applies to
    action_class    text NOT NULL            -- 'read' | 'draft' | 'commit' | 'destructive'
                        CHECK (action_class IN ('read','draft','commit','destructive')),
    -- the effect
    effect          text NOT NULL            -- 'allow' | 'deny' | 'require_approval' | 'require_mfa'
                        CHECK (effect IN ('allow','deny','require_approval','require_mfa')),
    -- optional conditions (all must match)
    min_amount      numeric(15,2),           -- applies when action.amount >= min_amount
    max_amount      numeric(15,2),           -- applies when action.amount <= max_amount
    roles_match     text[],                  -- empty = any role; otherwise role must be in list
    paths_match     text[],                  -- automation path (e.g. 'computer_use','browser')
    capabilities_match text[],              -- capability keys this rule covers
    -- approval config (used when effect = require_approval)
    approval_roles  text[] DEFAULT ARRAY['owner','manager'],
    approval_count  int DEFAULT 1,
    -- metadata
    is_active       bool NOT NULL DEFAULT true,
    priority        int NOT NULL DEFAULT 100,  -- lower = evaluated first
    created_by      uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_policy_rules_company_active
    ON policy_rules (company_id, is_active, priority);

ALTER TABLE policy_rules ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON policy_rules
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

-- Seed default policy rules for every new company
-- (Applied at registration time by the backend; these are safe defaults.)
-- actual seeding is done in app code so we don't hard-code company IDs here.

COMMENT ON TABLE policy_rules IS
    'Per-tenant automation policy rules. Evaluated in priority order. '
    'First matching rule wins. If no rule matches, default = allow for read, '
    'require_approval for commit/destructive.';


-- ---------------------------------------------------------------------------
-- G8 — File ingestion job lifecycle tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         uuid,
    -- source
    source_type     text NOT NULL            -- 'upload' | 'watched_folder' | 'email_attachment'
                        CHECK (source_type IN ('upload','watched_folder','email_attachment')),
    file_name       text NOT NULL,
    file_size_bytes bigint,
    file_mime_type  text,
    storage_path    text,                    -- cloud storage path or local path
    -- parsing
    parser_type     text,                    -- 'csv' | 'xlsx' | 'pdf' | 'auto'
    entity_type     text,                    -- 'inventory' | 'orders' | 'customers' | 'compliance' | 'unknown'
    -- status lifecycle
    status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','parsing','parsed','ingesting','completed',
                                          'failed','skipped')),
    rows_parsed     int DEFAULT 0,
    rows_ingested   int DEFAULT 0,
    rows_skipped    int DEFAULT 0,
    rows_errored    int DEFAULT 0,
    parse_warnings  jsonb DEFAULT '[]',      -- array of {row, column, message}
    error_message   text,
    -- confidence / lineage
    confidence_pct  smallint CHECK (confidence_pct BETWEEN 0 AND 100),
    lineage         jsonb DEFAULT '{}',      -- {source_file, parsed_at, schema_detected, etc.}
    -- timing
    enqueued_at     timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    duration_ms     int GENERATED ALWAYS AS (
        CASE WHEN completed_at IS NOT NULL AND started_at IS NOT NULL
             THEN EXTRACT(MILLISECONDS FROM (completed_at - started_at))::int
        END
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_company
    ON ingestion_jobs (company_id, enqueued_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status
    ON ingestion_jobs (status)
    WHERE status NOT IN ('completed','failed','skipped');

ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON ingestion_jobs
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

COMMENT ON TABLE ingestion_jobs IS
    'Tracks every file ingestion job from upload through parsing to DB insertion. '
    'lineage column captures source file metadata for audit trail.';


-- ---------------------------------------------------------------------------
-- G9 — Browser automation jobs (Playwright)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS browser_jobs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         uuid,
    automation_session_id uuid REFERENCES automation_sessions(id) ON DELETE SET NULL,
    -- job definition
    workflow_name   text NOT NULL,           -- e.g. 'customs_portal', 'distributor_portal'
    capability      text NOT NULL,           -- capability this job fulfills
    parameters      jsonb DEFAULT '{}',
    -- status
    status          text NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','awaiting_approval','completed',
                                          'failed','cancelled','timed_out')),
    result          jsonb,                   -- normalized extracted data
    confidence_pct  smallint CHECK (confidence_pct BETWEEN 0 AND 100),
    error_message   text,
    -- artifacts
    screenshot_urls jsonb DEFAULT '[]',
    action_log      jsonb DEFAULT '[]',      -- array of {action, url, timestamp, result}
    -- timing
    queued_at       timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    timeout_seconds int DEFAULT 120,
    retry_count     int DEFAULT 0,
    max_retries     int DEFAULT 2,
    -- correlation
    correlation_id  text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_browser_jobs_company
    ON browser_jobs (company_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS idx_browser_jobs_status
    ON browser_jobs (status)
    WHERE status NOT IN ('completed','failed','cancelled');

ALTER TABLE browser_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON browser_jobs
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);


-- ---------------------------------------------------------------------------
-- G10 — Privileged action audit events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         uuid,
    -- what happened
    event_type      text NOT NULL,           -- 'action.approved' | 'action.denied' | 'policy.violation'
                                             -- | 'auth.login' | 'auth.2fa_enabled' | 'connector.registered'
                                             -- | 'automation.started' | 'automation.killed' | ...
    action_class    text                     -- 'read' | 'draft' | 'commit' | 'destructive'
                        CHECK (action_class IN ('read','draft','commit','destructive',null)),
    capability      text,
    path_used       text,                    -- 'api' | 'browser' | 'file' | 'computer_use'
    -- context
    entity_type     text,                    -- 'order' | 'customer' | 'inventory' | ...
    entity_id       text,
    description     text NOT NULL,
    before_snapshot jsonb,
    after_snapshot  jsonb,
    -- policy
    policy_rule_id  uuid REFERENCES policy_rules(id) ON DELETE SET NULL,
    policy_effect   text,                    -- 'allow' | 'deny' | 'require_approval'
    -- approval
    approved_by     uuid,
    approved_at     timestamptz,
    -- request context
    ip_address      inet,
    user_agent      text,
    request_id      text,
    correlation_id  text,
    -- timing
    occurred_at     timestamptz NOT NULL DEFAULT now()
);

-- Audit table is append-only; disable UPDATE/DELETE via RLS
CREATE INDEX IF NOT EXISTS idx_audit_events_company
    ON audit_events (company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_type
    ON audit_events (company_id, event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_user
    ON audit_events (user_id, occurred_at DESC);

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
-- Only owners/managers can read audit events; no updates or deletes allowed
CREATE POLICY "company_read_only" ON audit_events
    FOR SELECT USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

COMMENT ON TABLE audit_events IS
    'Immutable append-only audit log for all privileged events. '
    'Never update or delete rows. Retention policy: minimum 1 year.';


-- ---------------------------------------------------------------------------
-- G15 — Per-tenant feature flags for risky automation modules
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS company_features (
    company_id          uuid PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    -- automation path enables
    computer_use_enabled    bool NOT NULL DEFAULT false,
    browser_auto_enabled    bool NOT NULL DEFAULT true,
    file_ingestion_enabled  bool NOT NULL DEFAULT true,
    -- safety settings
    computer_use_max_steps  int NOT NULL DEFAULT 50,
    browser_job_timeout_sec int NOT NULL DEFAULT 120,
    require_approval_above  numeric(15,2) DEFAULT 500.00,  -- auto-approve below this amount
    -- notification preferences
    alert_on_policy_violation   bool NOT NULL DEFAULT true,
    alert_on_connector_stale    bool NOT NULL DEFAULT true,
    connector_stale_minutes     int NOT NULL DEFAULT 10,
    -- billing tier
    feature_tier            text NOT NULL DEFAULT 'standard'
                                CHECK (feature_tier IN ('standard','professional','enterprise')),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    updated_by              uuid
);

-- Insert default row when a company is created (handled in app code)
ALTER TABLE company_features ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON company_features
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

COMMENT ON TABLE company_features IS
    'Per-tenant feature flags and safety thresholds. '
    'Replaces the global COMPUTER_USE_ENABLED environment variable.';


-- ---------------------------------------------------------------------------
-- Add missing columns to existing tables (non-breaking)
-- ---------------------------------------------------------------------------

-- Add correlation_id to workflow_runs for distributed tracing
ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS correlation_id text,
    ADD COLUMN IF NOT EXISTS policy_rule_id uuid REFERENCES policy_rules(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_by     uuid,
    ADD COLUMN IF NOT EXISTS approved_at     timestamptz;

-- Add path_used to access_router_log for cleaner querying
ALTER TABLE access_router_log
    ADD COLUMN IF NOT EXISTS duration_ms   int,
    ADD COLUMN IF NOT EXISTS confidence    smallint CHECK (confidence BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS correlation_id text;

-- Add device_push_tokens table if not already there (referenced by notifications.py)
CREATE TABLE IF NOT EXISTS device_push_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL,
    token       text NOT NULL,
    platform    text NOT NULL CHECK (platform IN ('ios','android','web')),
    created_at  timestamptz DEFAULT now(),
    UNIQUE (company_id, user_id, token)
);
ALTER TABLE device_push_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON device_push_tokens
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

-- Add report_snapshots if not already there (referenced by scheduler)
CREATE TABLE IF NOT EXISTS report_snapshots (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    report_type     text NOT NULL,
    payload         jsonb NOT NULL,
    generated_at    timestamptz NOT NULL DEFAULT now(),
    generated_by    text DEFAULT 'scheduler',
    CONSTRAINT uq_report_snapshots_company_type UNIQUE (company_id, report_type)
);
ALTER TABLE report_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON report_snapshots
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);
