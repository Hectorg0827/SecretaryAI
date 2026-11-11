-- FIXTURE ONLY — do not run against a real database.
-- The schema as it was created by the old database/schema.sql instructions
-- (before the launch reconcile). scripts/validate_schema.sh uses it to prove
-- that a database set up the old way upgrades cleanly.
-- SecretaryAI Database Schema
-- PostgreSQL / Supabase
-- Run this in the Supabase SQL editor to set up the database.

-- ============================================================
-- ENABLE EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ============================================================
-- COMPANIES  (one row per paying customer / tenant)
-- ============================================================
CREATE TABLE companies (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT NOT NULL,
    business_type           TEXT DEFAULT 'importer/distributor',
    qb_type                 TEXT CHECK (qb_type IN ('desktop', 'online')),
    qb_connection_status    TEXT DEFAULT 'disconnected',

    -- QB Desktop (Conductor)
    conductor_end_user_id   TEXT,

    -- QB Online (Intuit OAuth)
    qbo_realm_id            TEXT,
    qbo_access_token        TEXT,         -- stored encrypted at app layer
    qbo_refresh_token       TEXT,         -- stored encrypted at app layer
    qbo_token_expires_at    TIMESTAMPTZ,

    -- Preferences
    timezone                TEXT DEFAULT 'America/New_York',
    preferred_language      TEXT DEFAULT 'English',
    morning_briefing_enabled BOOLEAN DEFAULT TRUE,
    alert_email             TEXT,

    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email           TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    full_name       TEXT,
    role            TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('owner', 'manager', 'sales_rep', 'back_office', 'viewer')),
    totp_secret     TEXT,           -- 2FA secret (encrypted at app layer)
    totp_enabled    BOOLEAN DEFAULT FALSE,
    is_active       BOOLEAN DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- ACCOUNTS  (buying customers — synced from QB)
-- ============================================================
CREATE TABLE accounts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    qb_customer_id          TEXT,                   -- QB's internal ID
    name                    TEXT NOT NULL,
    email                   TEXT,
    phone                   TEXT,
    state                   TEXT,
    territory               TEXT,
    assigned_rep_id         UUID REFERENCES users(id),
    avg_order_cycle_days    INTEGER,
    avg_order_value         DECIMAL(12, 2),
    last_order_date         DATE,
    current_balance         DECIMAL(12, 2) DEFAULT 0,
    health_status           TEXT DEFAULT 'unknown'
                            CHECK (health_status IN ('healthy', 'slowing', 'at_risk', 'dormant', 'unknown')),
    health_score            INTEGER DEFAULT 0,
    last_health_check       TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, qb_customer_id)
);


-- ============================================================
-- ORDERS / INVOICES  (synced from QB)
-- ============================================================
CREATE TABLE orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    account_id      UUID REFERENCES accounts(id),
    qb_invoice_id   TEXT,
    order_date      DATE,
    due_date        DATE,
    total_amount    DECIMAL(12, 2),
    balance         DECIMAL(12, 2),
    status          TEXT DEFAULT 'open',
    items           JSONB,          -- [{product, qty, unit_price, amount}]
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, qb_invoice_id)
);


-- ============================================================
-- INVENTORY
-- ============================================================
CREATE TABLE inventory (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_name        TEXT NOT NULL,
    qb_item_id          TEXT,
    sku                 TEXT,
    warehouse_1_qty     INTEGER DEFAULT 0,    -- Primary warehouse (e.g., 3PL/FDL)
    warehouse_2_qty     INTEGER DEFAULT 0,    -- Secondary warehouse (self-managed)
    total_qty           INTEGER GENERATED ALWAYS AS (warehouse_1_qty + warehouse_2_qty) STORED,
    weekly_sell_rate    DECIMAL(10, 2) DEFAULT 0,
    weeks_remaining     DECIMAL(10, 2) GENERATED ALWAYS AS (
                            CASE
                                WHEN weekly_sell_rate > 0
                                THEN (warehouse_1_qty + warehouse_2_qty)::DECIMAL / weekly_sell_rate
                                ELSE NULL
                            END
                        ) STORED,
    stock_status        TEXT DEFAULT 'unknown'
                        CHECK (stock_status IN ('healthy', 'low', 'critical', 'out_of_stock', 'unknown')),
    open_po_qty         INTEGER DEFAULT 0,
    last_updated        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, qb_item_id)
);


-- ============================================================
-- CONVERSATIONS  (AI chat history)
-- ============================================================
CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id),
    title       TEXT,
    messages    JSONB NOT NULL DEFAULT '[]',   -- [{role, content, timestamp}]
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- ACTION LOG  (audit trail — every AI action is recorded)
-- ============================================================
CREATE TABLE action_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    actor           TEXT NOT NULL,          -- 'ai' or user UUID
    action_type     TEXT NOT NULL,
    autonomy_level  TEXT NOT NULL
                    CHECK (autonomy_level IN ('autonomous', 'notify', 'draft_and_wait', 'prohibited')),
    description     TEXT,
    data_involved   JSONB,
    status          TEXT NOT NULL
                    CHECK (status IN ('executed', 'pending_approval', 'approved', 'rejected', 'blocked')),
    approved_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- SYNC CHECKS  (QB ↔ SecretaryAI reconciliation)
-- ============================================================
CREATE TABLE sync_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    check_type      TEXT NOT NULL,          -- 'qb_accounts', 'qb_inventory', etc.
    source_1_value  DECIMAL,
    source_2_value  DECIMAL,
    variance_pct    DECIMAL,
    status          TEXT DEFAULT 'unknown'
                    CHECK (status IN ('healthy', 'warning', 'broken', 'unknown')),
    details         JSONB,
    checked_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- DRAFTS  (pending approval items from AI)
-- ============================================================
CREATE TABLE drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    created_by      TEXT NOT NULL,          -- 'ai' or user UUID
    action_type     TEXT NOT NULL,          -- 'draft_customer_email', 'draft_purchase_order', etc.
    content         JSONB NOT NULL,         -- the draft content
    status          TEXT DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'edited_and_approved')),
    reviewed_by     UUID REFERENCES users(id),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_accounts_company ON accounts(company_id);
CREATE INDEX idx_accounts_health ON accounts(company_id, health_status);
CREATE INDEX idx_orders_company_date ON orders(company_id, order_date DESC);
CREATE INDEX idx_orders_account ON orders(account_id, order_date DESC);
CREATE INDEX idx_inventory_company ON inventory(company_id);
CREATE INDEX idx_inventory_status ON inventory(company_id, stock_status);
CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);
CREATE INDEX idx_action_log_company ON action_log(company_id, created_at DESC);
CREATE INDEX idx_drafts_pending ON drafts(company_id, status) WHERE status = 'pending';


-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- Enforced at database level — prevents cross-tenant data leaks
-- even if application code has a bug.
-- ============================================================
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;

-- Users can only see their own company's data
CREATE POLICY "company_isolation" ON accounts
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON orders
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON inventory
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON conversations
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON action_log
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON sync_checks
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

CREATE POLICY "company_isolation" ON drafts
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

-- Users can only see other users in their own company
CREATE POLICY "company_users_isolation" ON users
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);


-- ============================================================
-- UPDATED_AT TRIGGER
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_companies_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
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
-- SecretaryAI V3 Integration Columns
-- Run AFTER schema_v2_additions.sql.
-- Adds timestamp tracking for OAuth connections.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS qbo_connected_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS google_connected_at    TIMESTAMPTZ;
