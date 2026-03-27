-- SecretaryAI — Core Schema Migration
-- Consolidates database/schema.sql + schema_v2_additions.sql + schema_v3_integrations.sql
-- Fixes column name mismatches and adds missing tables/columns.
-- Run this FIRST before billing and compliance migrations.

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ── companies ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                            TEXT NOT NULL,
    business_type                   TEXT DEFAULT 'importer/distributor',

    -- QuickBooks type + connection status
    qb_type                         TEXT CHECK (qb_type IN ('desktop', 'online')),
    qb_connection_status            TEXT DEFAULT 'disconnected',

    -- QB Desktop via Conductor
    conductor_end_user_id           TEXT,
    conductor_api_key               TEXT,   -- encrypted at app layer

    -- QB Online (Intuit OAuth)
    qbo_realm_id                    TEXT,
    qbo_access_token                TEXT,   -- encrypted
    qbo_refresh_token               TEXT,   -- encrypted
    qbo_token_expires_at            TIMESTAMPTZ,
    qbo_connected_at                TIMESTAMPTZ,
    qbo_token_refreshed_at          TIMESTAMPTZ,

    -- Google (Gmail + Sheets)
    google_access_token             TEXT,   -- encrypted
    google_refresh_token            TEXT,   -- encrypted
    google_connected_at             TIMESTAMPTZ,

    -- Microsoft / Outlook
    ms_tenant_id                    TEXT,
    ms_client_id                    TEXT,
    ms_client_secret                TEXT,   -- encrypted
    ms_access_token                 TEXT,   -- encrypted
    ms_refresh_token                TEXT,   -- encrypted

    -- Shopify
    shopify_shop_domain             TEXT,
    shopify_access_token            TEXT,   -- encrypted

    -- Shipping carriers
    fedex_api_key                   TEXT,   -- encrypted
    fedex_secret_key                TEXT,   -- encrypted
    ups_client_id                   TEXT,   -- encrypted
    ups_client_secret               TEXT,   -- encrypted
    dhl_api_key                     TEXT,   -- encrypted

    -- Computer Use / portals
    ordering_system_app             TEXT,
    warehouse_app                   TEXT,
    customs_portal_url              TEXT,
    customs_portal_name             TEXT,
    customs_portal_username         TEXT,
    customs_portal_password         TEXT,   -- encrypted
    computer_use_enabled            BOOLEAN DEFAULT TRUE,
    computer_use_permissions_granted BOOLEAN DEFAULT FALSE,
    watched_folders                 JSONB DEFAULT '[]',

    -- Compliance
    compliance_active_states        TEXT DEFAULT '',

    -- Preferences
    timezone                        TEXT DEFAULT 'America/New_York',
    preferred_language              TEXT DEFAULT 'English',
    morning_briefing_enabled        BOOLEAN DEFAULT TRUE,
    alert_email                     TEXT,

    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW()
);


-- ── users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,          -- NOTE: column is password_hash (not hashed_password)
    full_name       TEXT,
    name            TEXT,                   -- alias used by some queries
    role            TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('owner', 'manager', 'sales_rep', 'back_office', 'viewer')),
    totp_secret     TEXT,                   -- 2FA secret (encrypted at app layer)
    totp_enabled    BOOLEAN DEFAULT FALSE,
    is_active       BOOLEAN DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ── accounts ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    qb_customer_id          TEXT,
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


-- ── orders / invoices ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    account_id      UUID REFERENCES accounts(id),
    qb_invoice_id   TEXT,
    order_date      DATE,
    due_date        DATE,
    total_amount    DECIMAL(12, 2),
    balance         DECIMAL(12, 2),
    status          TEXT DEFAULT 'open',
    items           JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, qb_invoice_id)
);


-- ── inventory ─────────────────────────────────────────────────────────────────
-- NOTE: column is qb_id (not qb_item_id) — matches code in app/api/inventory.py
CREATE TABLE IF NOT EXISTS inventory (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_name        TEXT NOT NULL,
    qb_id               TEXT,           -- QB item ID (NOTE: was qb_item_id in old schema)
    sku                 TEXT,
    warehouse_1_qty     INTEGER DEFAULT 0,
    warehouse_2_qty     INTEGER DEFAULT 0,
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
    UNIQUE (company_id, qb_id)
);


-- ── conversations ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id),
    title       TEXT,
    messages    JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);


-- ── action_log ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    actor           TEXT NOT NULL,
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


-- ── sync_checks ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    check_type      TEXT NOT NULL,
    source_1_value  DECIMAL,
    source_2_value  DECIMAL,
    variance_pct    DECIMAL,
    status          TEXT DEFAULT 'unknown'
                    CHECK (status IN ('healthy', 'warning', 'broken', 'unknown')),
    details         JSONB,
    checked_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ── drafts ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS drafts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    created_by              TEXT NOT NULL,
    action_type             TEXT NOT NULL,
    content                 JSONB NOT NULL,
    status                  TEXT DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'edited_and_approved')),
    reviewed_by             UUID REFERENCES users(id),
    reviewed_at             TIMESTAMPTZ,
    rejection_reason        TEXT,
    computer_use_session_id UUID,   -- FK added after computer_use_sessions created
    created_at              TIMESTAMPTZ DEFAULT NOW()
);


-- ── computer_use_sessions ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS computer_use_sessions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id          UUID REFERENCES users(id),
    app_name         TEXT NOT NULL,
    task_description TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running', 'completed', 'failed', 'blocked', 'timed_out')),
    steps_taken      INTEGER DEFAULT 0,
    data_extracted   JSONB,
    error_message    TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    duration_seconds INTEGER GENERATED ALWAYS AS (
        CASE WHEN completed_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (completed_at - started_at))::INTEGER
        ELSE NULL END
    ) STORED
);

-- Now add the FK from drafts (after computer_use_sessions exists)
ALTER TABLE drafts
    ADD CONSTRAINT drafts_cu_session_fk
    FOREIGN KEY (computer_use_session_id)
    REFERENCES computer_use_sessions(id)
    ON DELETE SET NULL
    NOT VALID;


-- ── computer_use_blocked_actions ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS computer_use_blocked_actions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID REFERENCES computer_use_sessions(id) ON DELETE CASCADE,
    company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    action_attempted JSONB NOT NULL,
    block_reason     TEXT NOT NULL,
    blocked_at       TIMESTAMPTZ DEFAULT NOW()
);


-- ── ingested_files ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingested_files (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'unknown',
    row_count   INTEGER DEFAULT 0,
    data        JSONB DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);


-- ── access_router_log ─────────────────────────────────────────────────────────
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


-- ── agent_heartbeats ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    company_id    UUID PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    last_seen     TIMESTAMPTZ NOT NULL,
    agent_version TEXT,
    platform      TEXT,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);


-- ── follow_up_notes ───────────────────────────────────────────────────────────
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


-- ── morning_briefings ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS morning_briefings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    briefing_text   TEXT NOT NULL,
    recipient_email TEXT,
    sent_at         TIMESTAMPTZ,
    language        TEXT DEFAULT 'English',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ── report_snapshots ──────────────────────────────────────────────────────────
-- One row per (company_id, report_type); upserted by Celery tasks.
-- All employees read the same snapshot instead of each triggering a QB round-trip.
CREATE TABLE IF NOT EXISTS report_snapshots (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    report_type  TEXT NOT NULL,     -- 'dashboard', 'inventory_alerts', 'compliance_alerts', etc.
    payload      JSONB NOT NULL DEFAULT '{}',
    generated_at TIMESTAMPTZ NOT NULL,
    generated_by TEXT NOT NULL DEFAULT 'scheduler',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, report_type)
);


-- ── account_memory ────────────────────────────────────────────────────────────
-- Per-account facts extracted by AI during conversations (memory system).
CREATE TABLE IF NOT EXISTS account_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    account_id  TEXT NOT NULL,      -- QB customer ID or name
    facts       JSONB NOT NULL DEFAULT '[]',  -- [{fact: str, extracted_at: ISO}]
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, account_id)
);


-- ── conversation_summaries ────────────────────────────────────────────────────
-- Rolling 2-3 sentence summaries per user conversation thread.
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,
    message_count   INTEGER DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, user_id)
);


-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_accounts_company         ON accounts(company_id);
CREATE INDEX IF NOT EXISTS idx_accounts_health          ON accounts(company_id, health_status);
CREATE INDEX IF NOT EXISTS idx_orders_company_date      ON orders(company_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_orders_account           ON orders(account_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_company        ON inventory(company_id);
CREATE INDEX IF NOT EXISTS idx_inventory_status         ON inventory(company_id, stock_status);
CREATE INDEX IF NOT EXISTS idx_conversations_user       ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_log_company       ON action_log(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drafts_pending           ON drafts(company_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_cu_sessions_company      ON computer_use_sessions(company_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_cu_sessions_status       ON computer_use_sessions(status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_cu_blocked_session       ON computer_use_blocked_actions(session_id);
CREATE INDEX IF NOT EXISTS idx_ingested_files_company   ON ingested_files(company_id, report_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_router_log_company       ON access_router_log(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_follow_up_notes_company  ON follow_up_notes(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_report_snapshots_company ON report_snapshots(company_id, report_type);
CREATE INDEX IF NOT EXISTS idx_account_memory_company   ON account_memory(company_id, account_id);
CREATE INDEX IF NOT EXISTS idx_conv_summaries_user      ON conversation_summaries(company_id, user_id);
CREATE INDEX IF NOT EXISTS idx_morning_briefings_company ON morning_briefings(company_id, created_at DESC);


-- ── Row Level Security ────────────────────────────────────────────────────────
ALTER TABLE companies                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE users                       ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations               ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_log                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_checks                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE drafts                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE computer_use_sessions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE computer_use_blocked_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingested_files              ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_router_log           ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_heartbeats            ENABLE ROW LEVEL SECURITY;
ALTER TABLE follow_up_notes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE morning_briefings           ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_snapshots            ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_memory              ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_summaries      ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON accounts               FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON orders                 FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON inventory              FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON conversations          FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON action_log             FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON sync_checks            FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON drafts                 FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON computer_use_sessions  FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON computer_use_blocked_actions FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON ingested_files         FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON access_router_log      FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON agent_heartbeats       FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON follow_up_notes        FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON morning_briefings      FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON report_snapshots       FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON account_memory         FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_isolation" ON conversation_summaries FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE POLICY "company_users_isolation" ON users            FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);


-- ── updated_at trigger ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_companies_updated_at
    BEFORE UPDATE ON companies FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON accounts FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_report_snapshots_updated_at
    BEFORE UPDATE ON report_snapshots FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_account_memory_updated_at
    BEFORE UPDATE ON account_memory FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_follow_up_notes_updated_at
    BEFORE UPDATE ON follow_up_notes FOR EACH ROW EXECUTE FUNCTION update_updated_at();
