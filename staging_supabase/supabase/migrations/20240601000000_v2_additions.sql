-- SecretaryAI V2 Schema Additions
-- Run AFTER schema.sql (initial V1 schema).
-- Adds Computer Use sessions table and companies config columns.

-- ============================================================
-- COMPUTER USE SESSIONS  (audit trail for every CU task)
-- ============================================================
CREATE TABLE computer_use_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users(id),
    app_name        TEXT NOT NULL,              -- e.g., "ScribeBase"
    task_description TEXT NOT NULL,            -- what the AI was asked to find
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed', 'blocked', 'timed_out')),
    steps_taken     INTEGER DEFAULT 0,
    data_extracted  JSONB,                      -- what was found (null if failed)
    error_message   TEXT,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    duration_seconds INTEGER GENERATED ALWAYS AS (
        CASE WHEN completed_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (completed_at - started_at))::INTEGER
        ELSE NULL END
    ) STORED
);

-- ============================================================
-- COMPUTER USE BLOCKED ACTIONS  (safety audit)
-- ============================================================
CREATE TABLE computer_use_blocked_actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID REFERENCES computer_use_sessions(id) ON DELETE CASCADE,
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    action_attempted JSONB NOT NULL,           -- the full action params
    block_reason    TEXT NOT NULL,
    blocked_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ADD V2 COLUMNS TO EXISTING TABLES
-- ============================================================

-- Companies: add connector configs for Google APIs and Computer Use
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS google_access_token   TEXT,      -- encrypted
    ADD COLUMN IF NOT EXISTS google_refresh_token  TEXT,      -- encrypted
    ADD COLUMN IF NOT EXISTS ordering_system_app   TEXT,      -- e.g., "ScribeBase"
    ADD COLUMN IF NOT EXISTS warehouse_app         TEXT,      -- e.g., "FDL Portal"
    ADD COLUMN IF NOT EXISTS customs_portal_url    TEXT,
    ADD COLUMN IF NOT EXISTS customs_portal_name   TEXT,
    ADD COLUMN IF NOT EXISTS computer_use_enabled  BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS computer_use_permissions_granted BOOLEAN DEFAULT FALSE;

-- Drafts: add rejection reason and Computer Use session reference
ALTER TABLE drafts
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
    ADD COLUMN IF NOT EXISTS computer_use_session_id UUID REFERENCES computer_use_sessions(id);

-- ============================================================
-- INDEXES FOR NEW TABLES
-- ============================================================
CREATE INDEX idx_cu_sessions_company ON computer_use_sessions(company_id, started_at DESC);
CREATE INDEX idx_cu_sessions_status ON computer_use_sessions(status) WHERE status = 'running';
CREATE INDEX idx_cu_blocked_session ON computer_use_blocked_actions(session_id);

-- ============================================================
-- RLS FOR NEW TABLES
-- ============================================================
ALTER TABLE computer_use_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE computer_use_blocked_actions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON computer_use_sessions
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON computer_use_blocked_actions
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

-- ============================================================
-- V3 ADDITIONS: Access Router + File Ingestion + Agent Heartbeat
-- ============================================================

-- Watched folders per company (file ingestion)
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS watched_folders JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS customs_portal_username TEXT,
    ADD COLUMN IF NOT EXISTS customs_portal_password TEXT;  -- encrypted

-- Ingested file records (cache for file-ingestion access path)
CREATE TABLE IF NOT EXISTS ingested_files (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'unknown',
    row_count   INTEGER DEFAULT 0,
    data        JSONB DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ingested_files ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON ingested_files
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE INDEX idx_ingested_files_company_type ON ingested_files(company_id, report_type, created_at DESC);

-- Access router decision log
CREATE TABLE IF NOT EXISTS access_router_log (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id     UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    capability     TEXT NOT NULL,
    operation_type TEXT NOT NULL DEFAULT 'read',
    path_tried     TEXT NOT NULL,
    success        BOOLEAN NOT NULL,
    fallback_used  BOOLEAN DEFAULT FALSE,
    error_msg      TEXT,
    duration_ms    INTEGER,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE access_router_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON access_router_log
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE INDEX idx_router_log_company ON access_router_log(company_id, created_at DESC);

-- Desktop agent heartbeat (one row per company, upserted)
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    company_id    UUID PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    last_seen     TIMESTAMPTZ NOT NULL,
    agent_version TEXT,
    platform      TEXT,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE agent_heartbeats ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON agent_heartbeats
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

-- ============================================================
-- FOLLOW-UP NOTES  (dashboard notes panel)
-- ============================================================
CREATE TABLE IF NOT EXISTS follow_up_notes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    text         TEXT NOT NULL,
    account_name TEXT,
    due_date     DATE,
    done         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE follow_up_notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation_notes" ON follow_up_notes
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE INDEX idx_follow_up_notes_company ON follow_up_notes(company_id, created_at DESC);
