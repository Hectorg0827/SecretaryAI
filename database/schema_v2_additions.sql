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
