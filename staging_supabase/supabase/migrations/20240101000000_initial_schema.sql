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
