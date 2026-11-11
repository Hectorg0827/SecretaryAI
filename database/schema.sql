-- ═════════════════════════════════════════════════════════════════════
-- SecretaryAI database schema — GENERATED FILE, do not edit by hand.
-- Source: supabase/migrations/*.sql   Rebuild: scripts/build_schema.sh
-- Safe to run on a new database or to bring an existing one up to date.
-- ═════════════════════════════════════════════════════════════════════

-- ▶ 20240101000000_initial_schema.sql
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
CREATE TABLE IF NOT EXISTS companies (
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
CREATE TABLE IF NOT EXISTS users (
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
CREATE TABLE IF NOT EXISTS accounts (
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
    items           JSONB,          -- [{product, qty, unit_price, amount}]
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, qb_invoice_id)
);


-- ============================================================
-- INVENTORY
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
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
CREATE TABLE IF NOT EXISTS conversations (
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
CREATE TABLE IF NOT EXISTS action_log (
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
CREATE TABLE IF NOT EXISTS sync_checks (
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
CREATE TABLE IF NOT EXISTS drafts (
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
CREATE INDEX IF NOT EXISTS idx_accounts_company ON accounts(company_id);
CREATE INDEX IF NOT EXISTS idx_accounts_health ON accounts(company_id, health_status);
CREATE INDEX IF NOT EXISTS idx_orders_company_date ON orders(company_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_company ON inventory(company_id);
CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory(company_id, stock_status);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_log_company ON action_log(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drafts_pending ON drafts(company_id, status) WHERE status = 'pending';


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
DROP POLICY IF EXISTS "company_isolation" ON accounts;
CREATE POLICY "company_isolation" ON accounts
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON orders;
CREATE POLICY "company_isolation" ON orders
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON inventory;
CREATE POLICY "company_isolation" ON inventory
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON conversations;
CREATE POLICY "company_isolation" ON conversations
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON action_log;
CREATE POLICY "company_isolation" ON action_log
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON sync_checks;
CREATE POLICY "company_isolation" ON sync_checks
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON drafts;
CREATE POLICY "company_isolation" ON drafts
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

-- Users can only see other users in their own company
DROP POLICY IF EXISTS "company_users_isolation" ON users;
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

DROP TRIGGER IF EXISTS trg_companies_updated_at ON companies;
CREATE TRIGGER trg_companies_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_accounts_updated_at ON accounts;
CREATE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_conversations_updated_at ON conversations;
CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ▶ 20240601000000_v2_additions.sql
-- SecretaryAI V2 Schema Additions
-- Run AFTER schema.sql (initial V1 schema).
-- Adds Computer Use sessions table and companies config columns.

-- ============================================================
-- COMPUTER USE SESSIONS  (audit trail for every CU task)
-- ============================================================
CREATE TABLE IF NOT EXISTS computer_use_sessions (
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
CREATE TABLE IF NOT EXISTS computer_use_blocked_actions (
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
CREATE INDEX IF NOT EXISTS idx_cu_sessions_company ON computer_use_sessions(company_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_cu_sessions_status ON computer_use_sessions(status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_cu_blocked_session ON computer_use_blocked_actions(session_id);

-- ============================================================
-- RLS FOR NEW TABLES
-- ============================================================
ALTER TABLE computer_use_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE computer_use_blocked_actions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "company_isolation" ON computer_use_sessions;
CREATE POLICY "company_isolation" ON computer_use_sessions
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

DROP POLICY IF EXISTS "company_isolation" ON computer_use_blocked_actions;
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
DROP POLICY IF EXISTS "company_isolation" ON ingested_files;
CREATE POLICY "company_isolation" ON ingested_files
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE INDEX IF NOT EXISTS idx_ingested_files_company_type ON ingested_files(company_id, report_type, created_at DESC);

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
DROP POLICY IF EXISTS "company_isolation" ON access_router_log;
CREATE POLICY "company_isolation" ON access_router_log
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE INDEX IF NOT EXISTS idx_router_log_company ON access_router_log(company_id, created_at DESC);

-- Desktop agent heartbeat (one row per company, upserted)
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    company_id    UUID PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    last_seen     TIMESTAMPTZ NOT NULL,
    agent_version TEXT,
    platform      TEXT,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE agent_heartbeats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "company_isolation" ON agent_heartbeats;
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
DROP POLICY IF EXISTS "company_isolation_notes" ON follow_up_notes;
CREATE POLICY "company_isolation_notes" ON follow_up_notes
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
CREATE INDEX IF NOT EXISTS idx_follow_up_notes_company ON follow_up_notes(company_id, created_at DESC);

-- ▶ 20240901000000_v3_integrations.sql
-- SecretaryAI V3 Integration Columns
-- Run AFTER schema_v2_additions.sql.
-- Adds timestamp tracking for OAuth connections.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS qbo_connected_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS google_connected_at    TIMESTAMPTZ;

-- ▶ 20250322000000_draft_expiry.sql
-- Migration: add expires_at to drafts table for approval expiry.
-- Pending drafts past this timestamp are archived by the nightly task.
--
-- This is a plain column the application sets (default: 48 hours after
-- creation). It was previously declared as a GENERATED column, which Postgres
-- rejects ("generation expression is not immutable" — timestamptz + interval
-- depends on the session time zone), so the column never existed and every
-- draft insert failed.

ALTER TABLE drafts
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT (now() + INTERVAL '48 hours');

CREATE INDEX IF NOT EXISTS idx_drafts_expires_at
  ON drafts (expires_at)
  WHERE status = 'pending';

-- ▶ 20250322000001_ingested_files_hash.sql
-- Migration: add file_hash to ingested_files for deduplication (Tier 2-11)
-- SHA-256 of filename + row_count + first-row fingerprint.
-- Unique per company to prevent the same file being ingested twice.

ALTER TABLE ingested_files
  ADD COLUMN IF NOT EXISTS file_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_files_hash
  ON ingested_files (company_id, file_hash)
  WHERE file_hash IS NOT NULL;

-- ▶ 20250325000000_report_snapshots.sql
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

DROP POLICY IF EXISTS "report_snapshots_company_isolation" ON report_snapshots;
CREATE POLICY "report_snapshots_company_isolation" ON report_snapshots
    FOR ALL
    USING (
        company_id = (
            SELECT company_id FROM users WHERE id = auth.uid()
        )
    );

-- ▶ 20250325000001_memory.sql
CREATE TABLE IF NOT EXISTS account_memory (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    account_name text NOT NULL,
    facts        jsonb NOT NULL DEFAULT '[]',  -- array of {fact: str, confidence: float, updated_at: str}
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, account_name)
);
CREATE INDEX IF NOT EXISTS account_memory_company_idx ON account_memory (company_id);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id         text NOT NULL,
    conversation_id text NOT NULL UNIQUE,
    summary         text NOT NULL,
    account_names   text[] DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conv_summaries_company_user_idx ON conversation_summaries (company_id, user_id);

-- RLS
ALTER TABLE account_memory ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "account_memory_company_isolation" ON account_memory;
CREATE POLICY "account_memory_company_isolation" ON account_memory
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

ALTER TABLE conversation_summaries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "conv_summaries_company_isolation" ON conversation_summaries;
CREATE POLICY "conv_summaries_company_isolation" ON conversation_summaries
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

-- ▶ 20250325000002_feed_events.sql
CREATE TABLE IF NOT EXISTS feed_events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    event_type   text NOT NULL,  -- 'dormant_account' | 'low_stock' | 'overdue_invoice' | 'anomaly' | 'first_order'
    title        text NOT NULL,
    body         text NOT NULL,
    priority     text NOT NULL DEFAULT 'medium',  -- 'high' | 'medium' | 'low'
    action_label text,           -- e.g. 'Draft Outreach'
    action_type  text,           -- e.g. 'draft_customer_email'
    action_data  jsonb DEFAULT '{}',
    entity_id    text,           -- account_name or item_id
    entity_name  text,
    is_read      boolean NOT NULL DEFAULT false,
    is_dismissed boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz
);
CREATE INDEX IF NOT EXISTS feed_events_company_idx ON feed_events (company_id, is_dismissed, created_at DESC);
CREATE INDEX IF NOT EXISTS feed_events_entity_idx ON feed_events (company_id, entity_id);

ALTER TABLE feed_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "feed_events_company_isolation" ON feed_events;
CREATE POLICY "feed_events_company_isolation" ON feed_events
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

-- ▶ 20250325000003_workflows.sql
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
DROP POLICY IF EXISTS "workflow_runs_company_isolation" ON workflow_runs;
CREATE POLICY "workflow_runs_company_isolation" ON workflow_runs
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

-- ▶ 20250325000004_connector_fields.sql
-- Outlook / O365
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_tenant_id text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_client_id text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_client_secret text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_access_token text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_refresh_token text;

-- Shopify
ALTER TABLE companies ADD COLUMN IF NOT EXISTS shopify_shop_domain text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS shopify_access_token text;

-- Shipment tracking
ALTER TABLE companies ADD COLUMN IF NOT EXISTS fedex_api_key text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS fedex_secret_key text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ups_client_id text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ups_client_secret text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS dhl_api_key text;

-- ▶ 20250325000005_conductor_end_user.sql
-- Store the Conductor EndUser ID so we can route QB Desktop API calls
-- and avoid creating duplicate EndUsers on retries.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS conductor_end_user_id TEXT;

COMMENT ON COLUMN companies.conductor_end_user_id IS
  'Conductor (conductor.is) EndUser ID — links this company to their QB Desktop Web Connector session.';

-- ▶ 20250326000001_billing.sql
-- Subscription plans
create table if not exists subscription_plans (
  id text primary key,           -- 'starter' | 'growth' | 'enterprise'
  name text not null,
  price_monthly_usd numeric(10,2) not null,
  price_yearly_usd numeric(10,2) not null,
  max_users int not null default 5,
  max_skus int not null default 500,
  features jsonb not null default '[]',
  stripe_price_id_monthly text,
  stripe_price_id_yearly text,
  created_at timestamptz default now()
);

-- Company subscriptions
create table if not exists company_subscriptions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  plan_id text not null references subscription_plans(id),
  status text not null default 'active',  -- active | past_due | canceled | trialing
  stripe_customer_id text,
  stripe_subscription_id text,
  current_period_start timestamptz,
  current_period_end timestamptz,
  trial_end timestamptz,
  cancel_at_period_end boolean default false,
  seats_used int not null default 1,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Billing events / invoice history
create table if not exists billing_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  event_type text not null,   -- invoice.paid | invoice.payment_failed | subscription.updated | etc.
  amount_usd numeric(10,2),
  stripe_event_id text unique,
  metadata jsonb default '{}',
  created_at timestamptz default now()
);

-- Seed plans
insert into subscription_plans (id, name, price_monthly_usd, price_yearly_usd, max_users, max_skus, features)
values
  ('starter',    'Starter',    99,  990,  3,   500,  '["QB sync","AI chat","inventory alerts","email digest"]'),
  ('growth',     'Growth',     249, 2490, 10,  2000, '["QB sync","AI chat","inventory alerts","email digest","logistics pipeline","proactive intelligence","workflow automation"]'),
  ('enterprise', 'Enterprise', 599, 5990, 50,  10000,'["QB sync","AI chat","inventory alerts","email digest","logistics pipeline","proactive intelligence","workflow automation","multi-location","API access","dedicated support"]')
on conflict (id) do nothing;

-- ▶ 20250326000002_compliance.sql
-- Compliance Engine DB Migration
-- Creates tables for tracking alcohol beverage compliance entities.

-- Products tracked for compliance
CREATE TABLE IF NOT EXISTS compliance_products (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sku text NOT NULL,
    name text NOT NULL,
    product_type text NOT NULL,  -- 'wine'|'spirits'|'beer'|'malt'
    abv_pct numeric(5,2) DEFAULT 0,
    container_size_ml numeric(8,2) DEFAULT 750,
    cases_per_container int DEFAULT 56,
    unit_cost_fob numeric(10,2) DEFAULT 0,
    country_of_origin text DEFAULT '',
    foreign_producer_id text DEFAULT '',
    requires_formula_approval boolean DEFAULT false,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- Federal permits (TTB, FDA, CBP)
CREATE TABLE IF NOT EXISTS compliance_federal_permits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    permit_type text NOT NULL,
    permit_number text NOT NULL,
    issue_date date,
    expiration_date date,
    status text DEFAULT 'active',
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- TTB COLA records
CREATE TABLE IF NOT EXISTS compliance_colas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id uuid NOT NULL,
    cola_number text NOT NULL,
    product_type text NOT NULL,
    issue_date date,
    expiration_date date,
    status text DEFAULT 'active',
    formula_approved boolean DEFAULT false,
    lab_analysis_on_file boolean DEFAULT false,
    created_at timestamptz DEFAULT now()
);

-- State licenses
CREATE TABLE IF NOT EXISTS compliance_licenses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    state_code text NOT NULL,
    license_type text NOT NULL,
    product_types text[] DEFAULT '{}',
    license_number text NOT NULL,
    issue_date date,
    expiration_date date,
    renewal_window_days int DEFAULT 90,
    annual_fee numeric(10,2) DEFAULT 0,
    status text DEFAULT 'active',
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Brand registrations (per product per state)
CREATE TABLE IF NOT EXISTS compliance_brand_registrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id uuid NOT NULL,
    state_code text NOT NULL,
    registration_number text DEFAULT '',
    registration_date date,
    expiration_date date,
    registration_fee numeric(10,2) DEFAULT 0,
    status text DEFAULT 'active',
    state_label_approval_number text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Distributor relationships
CREATE TABLE IF NOT EXISTS compliance_distributors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    state_code text NOT NULL,
    distributor_name text NOT NULL,
    territory text DEFAULT 'Statewide',
    product_types text[] DEFAULT '{}',
    contract_start_date date,
    contract_end_date date,
    franchise_law_attached boolean DEFAULT false,
    franchise_attachment_date date,
    termination_restriction text DEFAULT 'none',
    contract_document_ref text DEFAULT '',
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Compliance check audit trail
CREATE TABLE IF NOT EXISTS compliance_check_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id uuid,
    state_code text,
    quantity_cases int DEFAULT 1,
    approved boolean NOT NULL,
    issues_count int DEFAULT 0,
    blockers_count int DEFAULT 0,
    total_fees numeric(10,2) DEFAULT 0,
    checked_at timestamptz DEFAULT now()
);

-- Add compliance_active_states to companies if not present
ALTER TABLE companies ADD COLUMN IF NOT EXISTS compliance_active_states text DEFAULT '';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_compliance_products_company ON compliance_products(company_id);
CREATE INDEX IF NOT EXISTS idx_compliance_licenses_company_state ON compliance_licenses(company_id, state_code);
CREATE INDEX IF NOT EXISTS idx_compliance_brand_reg_company ON compliance_brand_registrations(company_id, state_code);
CREATE INDEX IF NOT EXISTS idx_compliance_colas_company ON compliance_colas(company_id, product_id);

-- ▶ 20250326000003_procurement_cycles.sql
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

DROP POLICY IF EXISTS "company_isolation" ON procurement_cycles;
CREATE POLICY "company_isolation" ON procurement_cycles
    USING (company_id = (current_setting('app.company_id', true))::uuid);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_procurement_cycles_company
    ON procurement_cycles(company_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_procurement_cycles_po
    ON procurement_cycles(company_id, po_number);

-- auto-update updated_at
DROP TRIGGER IF EXISTS set_updated_at ON procurement_cycles;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON procurement_cycles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ▶ 20250326000004_qbo_token_expiry.sql
-- Track QBO access token expiry so the background refresh task knows which
-- tokens need to be renewed proactively.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS qbo_token_expires_at TIMESTAMPTZ;

COMMENT ON COLUMN companies.qbo_token_expires_at IS
    'UTC timestamp when the QBO access token expires. '
    'Updated on every token exchange or refresh. '
    'Used by the qbo_token_refresh Celery task.';

-- ▶ 20250326000005_phase1_schema.sql
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
DROP POLICY IF EXISTS "company_isolation" ON connector_registrations;
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
DROP POLICY IF EXISTS "company_isolation" ON connector_sync_logs;
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
DROP POLICY IF EXISTS "company_isolation" ON automation_sessions;
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
DROP POLICY IF EXISTS "company_isolation" ON policy_rules;
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
DROP POLICY IF EXISTS "company_isolation" ON ingestion_jobs;
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
DROP POLICY IF EXISTS "company_isolation" ON browser_jobs;
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
DROP POLICY IF EXISTS "company_read_only" ON audit_events;
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
DROP POLICY IF EXISTS "company_isolation" ON company_features;
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
DROP POLICY IF EXISTS "company_isolation" ON device_push_tokens;
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
DROP POLICY IF EXISTS "company_isolation" ON report_snapshots;
CREATE POLICY "company_isolation" ON report_snapshots
    FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::uuid);

-- ▶ 20250326000006_phase4_capability_routes.sql
-- Phase 4: AccessRouter hardening
-- ---------------------------------------------------------------------------
-- Add capability_routes JSONB to company_features so each tenant can
-- override which path chains are tried per capability.
--
-- Format: {"inventory": ["api", "file_ingestion"], "customers": ["api"]}
-- Keys are capability names; values are ordered lists of path names.
-- If a capability is absent, the built-in default chain is used.
-- ---------------------------------------------------------------------------

ALTER TABLE company_features
    ADD COLUMN IF NOT EXISTS capability_routes jsonb DEFAULT '{}';

COMMENT ON COLUMN company_features.capability_routes IS
    'Per-tenant capability → path chain overrides for AccessRouter. '
    'Keys: capability names. Values: ordered list of path names '
    '(api, playwright, file_ingestion, computer_use). '
    'Absent capabilities fall back to the built-in defaults.';

-- Also add correlation_id to browser_jobs for end-to-end tracing
ALTER TABLE browser_jobs
    ADD COLUMN IF NOT EXISTS correlation_id text;

CREATE INDEX IF NOT EXISTS idx_browser_jobs_correlation
    ON browser_jobs (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- ▶ 20250326000007_phase6_ingestion.sql
-- Phase 6: Browser automation job queue + hardened ingestion queue
-- ─────────────────────────────────────────────────────────────────

-- ── browser_automation_jobs ──────────────────────────────────────────────────
-- DB-backed job queue for browser automation tasks.
-- Replaces inline fire-and-forget calls with a durable, retriable queue.

CREATE TABLE IF NOT EXISTS browser_automation_jobs (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       uuid        NOT NULL,
    capability       text        NOT NULL,          -- e.g. 'accounts_receivable'
    parameters       jsonb       NOT NULL DEFAULT '{}',
    status           text        NOT NULL DEFAULT 'pending',
                                 -- pending | running | completed | failed | cancelled
    priority         int         NOT NULL DEFAULT 5, -- 1=highest, 10=lowest
    correlation_id   text,
    attempt_count    int         NOT NULL DEFAULT 0,
    max_attempts     int         NOT NULL DEFAULT 3,
    result           jsonb,
    confidence_pct   int,
    error_message    text,
    worker_id        text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    completed_at     timestamptz,
    next_retry_at    timestamptz,

    CONSTRAINT ba_jobs_status_check CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
    CONSTRAINT ba_jobs_priority_check CHECK (priority BETWEEN 1 AND 10),
    CONSTRAINT ba_jobs_confidence_check CHECK (
        confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100
    )
);

CREATE INDEX IF NOT EXISTS idx_ba_jobs_company_status
    ON browser_automation_jobs (company_id, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_ba_jobs_correlation
    ON browser_automation_jobs (correlation_id)
    WHERE correlation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ba_jobs_retry
    ON browser_automation_jobs (next_retry_at)
    WHERE status = 'failed' AND next_retry_at IS NOT NULL;


-- ── ingestion_queue ──────────────────────────────────────────────────────────
-- Tracks each file seen by the ingestion scanner.
-- Deduplication: (company_id, file_path, file_hash) is unique — a file is only
-- re-processed if its content changes.

CREATE TABLE IF NOT EXISTS ingestion_queue (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          uuid        NOT NULL,
    file_path           text        NOT NULL,
    file_hash           text        NOT NULL,    -- SHA-256 of file content
    file_size           bigint,
    entity_type         text,                   -- customers | invoices | inventory | unknown
    status              text        NOT NULL DEFAULT 'pending',
                                    -- pending | processing | done | error | skipped
    rows_ingested       int         NOT NULL DEFAULT 0,
    error_detail        text,
    attempt_count       int         NOT NULL DEFAULT 0,
    max_attempts        int         NOT NULL DEFAULT 3,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_processed_at   timestamptz,
    next_retry_at       timestamptz,

    CONSTRAINT iq_status_check CHECK (
        status IN ('pending', 'processing', 'done', 'error', 'skipped')
    ),
    UNIQUE (company_id, file_path, file_hash)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_queue_company_status
    ON ingestion_queue (company_id, status, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_queue_retry
    ON ingestion_queue (next_retry_at)
    WHERE status = 'error' AND next_retry_at IS NOT NULL;

-- ▶ 20250326000008_phase7_cu.sql
-- Phase 7: Computer-use job queue + result cache
-- ────────────────────────────────────────────────

-- computer_use_jobs: durable queue for CU tasks + result store.
--
-- Design decisions:
--   - max_attempts=2 (CU is expensive; fewer retries than browser jobs)
--   - expires_at: completed results expire after 1 hour; router ignores stale
--   - screenshot_ref: stores the screenshot key/path for audit trail attachment
--   - Index on (company_id, capability, completed_at) WHERE completed lets the
--     router check for a cached result with a single indexed scan

CREATE TABLE IF NOT EXISTS computer_use_jobs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid        NOT NULL,
    capability      text        NOT NULL,
    parameters      jsonb       NOT NULL DEFAULT '{}',
    status          text        NOT NULL DEFAULT 'pending',
                                -- pending | running | completed | failed | cancelled
    priority        int         NOT NULL DEFAULT 5,
    correlation_id  text,
    attempt_count   int         NOT NULL DEFAULT 0,
    max_attempts    int         NOT NULL DEFAULT 2,
    result          jsonb,
    confidence_pct  int,
    screenshot_ref  text,       -- storage key for screenshot (audit evidence)
    error_message   text,
    worker_id       text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    expires_at      timestamptz,  -- router ignores results older than this

    CONSTRAINT cu_status_check CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
    CONSTRAINT cu_priority_check CHECK (priority BETWEEN 1 AND 10),
    CONSTRAINT cu_confidence_check CHECK (
        confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100
    )
);

-- Fast cache-hit check: most-recent completed result for (company, capability)
CREATE INDEX IF NOT EXISTS idx_cu_jobs_cache
    ON computer_use_jobs (company_id, capability, completed_at DESC)
    WHERE status = 'completed';

-- Worker claim queue
CREATE INDEX IF NOT EXISTS idx_cu_jobs_pending
    ON computer_use_jobs (company_id, priority, created_at)
    WHERE status = 'pending';

-- Retry-eligible failed jobs
CREATE INDEX IF NOT EXISTS idx_cu_jobs_failed_retry
    ON computer_use_jobs (company_id, created_at)
    WHERE status = 'failed';

-- ▶ 20261111000000_launch_reconcile.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Launch reconcile — bring the database in line with what the code uses.
--
-- Before this file the schema lived in three folders that disagreed with each
-- other and with the code. Sign-up, chat history, the approval queue and the
-- audit log all wrote columns that did not exist. This migration closes every
-- gap found by scripts/check_schema_usage.py, and is safe to run more than
-- once and on a database created from the old `database/schema.sql`.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── users ────────────────────────────────────────────────────────────────────
-- The code has always read/written `password_hash` and `name`.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='users' AND column_name='hashed_password')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='users' AND column_name='password_hash') THEN
        ALTER TABLE users RENAME COLUMN hashed_password TO password_hash;
    END IF;
END $$;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT;
UPDATE users SET name = full_name WHERE name IS NULL AND full_name IS NOT NULL;
-- Invited users have no password until they accept the invite.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_by UUID;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
-- One account per email address regardless of capitalisation.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users (lower(email));
CREATE INDEX IF NOT EXISTS idx_users_company ON users (company_id);

-- ── companies ────────────────────────────────────────────────────────────────
ALTER TABLE companies ADD COLUMN IF NOT EXISTS qbo_token_refreshed_at TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS qbd_last_sync_at TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS compliance_active_states TEXT DEFAULT '';
-- Connector install secret: only a SHA-256 digest is stored, never the secret.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS connector_install_secret_hash TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS connector_install_secret_created_at TIMESTAMPTZ;
-- Microsoft / Outlook OAuth bookkeeping
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_connected_at TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_account_email TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_token_expires_at TIMESTAMPTZ;
-- A QuickBooks Desktop (Conductor) end-user ID is the key to one company's
-- books. It can never be attached to two companies.
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_conductor_end_user
    ON companies (conductor_end_user_id) WHERE conductor_end_user_id IS NOT NULL;
-- Same for a QuickBooks Online company (realm).
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_qbo_realm
    ON companies (qbo_realm_id) WHERE qbo_realm_id IS NOT NULL;

-- ── company_features ─────────────────────────────────────────────────────────
ALTER TABLE company_features ADD COLUMN IF NOT EXISTS api_auto_sync BOOLEAN DEFAULT TRUE;

-- ── conversations: one row per chat message ──────────────────────────────────
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_id TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS content TEXT;
CREATE INDEX IF NOT EXISTS idx_conversations_thread
    ON conversations (company_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations (company_id, user_id, created_at DESC);

-- ── accounts / inventory ─────────────────────────────────────────────────────
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS health_event_acknowledged BOOLEAN DEFAULT FALSE;

-- ── drafts: the approval queue ───────────────────────────────────────────────
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT (now() + INTERVAL '48 hours');
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS workflow_run_id UUID;
-- What actually happened after approval (so "approved" never hides a failure).
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS execution_status TEXT;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS execution_detail TEXT;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ;
ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_status_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_status_check CHECK (status IN (
    'pending', 'executing', 'approved', 'edited_and_approved', 'rejected', 'expired', 'failed'));
ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_execution_status_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_execution_status_check CHECK (
    execution_status IS NULL OR execution_status IN ('executed', 'manual_required', 'failed', 'unknown'));
CREATE INDEX IF NOT EXISTS idx_drafts_company_status ON drafts (company_id, status, created_at DESC);

-- ── action_log: the engine's own trail ───────────────────────────────────────
ALTER TABLE action_log DROP CONSTRAINT IF EXISTS action_log_status_check;
ALTER TABLE action_log ADD CONSTRAINT action_log_status_check CHECK (status IN (
    'pending', 'executed', 'pending_approval', 'approved', 'rejected', 'blocked', 'failed'));

-- ── audit_events: the durable, append-only audit trail ───────────────────────
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS actor_id TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE audit_events ALTER COLUMN description SET DEFAULT '';
-- actor / approver may be a person (UUID) or a system actor ("ai", "system",
-- "scheduler"), so they are stored as text.
ALTER TABLE audit_events ALTER COLUMN approved_by TYPE TEXT USING approved_by::text;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='audit_events' AND column_name='occurred_at') THEN
        ALTER TABLE audit_events ALTER COLUMN occurred_at SET DEFAULT now();
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_audit_events_company_created ON audit_events (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events (company_id, event_type, created_at DESC);

-- Audit rows can be added but never changed or removed — not even by the
-- backend's service-role key. (Deleting a whole company still cascades.)
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- Allow only the cascade from deleting the owning company.
        IF NOT EXISTS (SELECT 1 FROM companies WHERE id = OLD.company_id) THEN
            RETURN OLD;
        END IF;
    END IF;
    RAISE EXCEPTION 'audit_events is append-only (% not allowed)', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events;
CREATE TRIGGER trg_audit_events_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

-- ── workflow_runs ────────────────────────────────────────────────────────────
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
DROP TRIGGER IF EXISTS trg_workflow_runs_updated_at ON workflow_runs;
CREATE TRIGGER trg_workflow_runs_updated_at
    BEFORE UPDATE ON workflow_runs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── company_subscriptions: one row per company ───────────────────────────────
DELETE FROM company_subscriptions a USING company_subscriptions b
    WHERE a.company_id = b.company_id AND a.created_at < b.created_at;
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_subscriptions_company ON company_subscriptions (company_id);

-- ── connector_tasks: work queue for a local connector ────────────────────────
CREATE TABLE IF NOT EXISTS connector_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         TEXT NOT NULL,
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    connector_type  TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    parameters      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending', 'dispatched', 'running', 'completed', 'failed', 'cancelled', 'timed_out')),
    priority        INT NOT NULL DEFAULT 5,
    timeout_seconds INT NOT NULL DEFAULT 300,
    correlation_id  TEXT NOT NULL,
    requested_by    TEXT,
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    dispatched_at   TIMESTAMPTZ,
    dispatcher_id   UUID,
    completed_at    TIMESTAMPTZ,
    result_data     JSONB,
    rows_returned   INT DEFAULT 0,
    error_message   TEXT,
    error_code      TEXT,
    duration_ms     INT
);
CREATE INDEX IF NOT EXISTS idx_connector_tasks_queue
    ON connector_tasks (company_id, connector_type, status, priority, issued_at);

-- ── emails: triaged inbox items shown in the unified inbox ───────────────────
CREATE TABLE IF NOT EXISTS emails (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    provider         TEXT,
    provider_id      TEXT,
    thread_id        TEXT,
    from_name        TEXT,
    from_email       TEXT,
    subject          TEXT,
    snippet          TEXT,
    ai_priority      TEXT,
    ai_summary       TEXT,
    ai_action_needed TEXT,
    received_at      TIMESTAMPTZ,
    is_read          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, provider, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_emails_company_received ON emails (company_id, received_at DESC);

-- ── uploaded_reports: files sent up by the desktop folder watcher ────────────
CREATE TABLE IF NOT EXISTS uploaded_reports (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    uploaded_by  TEXT,
    file_name    TEXT NOT NULL,
    content_type TEXT,
    size_bytes   BIGINT,
    status       TEXT NOT NULL DEFAULT 'pending',
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_uploaded_reports_company ON uploaded_reports (company_id, uploaded_at DESC);

-- ── morning_briefings: a copy of every briefing that was sent ────────────────
CREATE TABLE IF NOT EXISTS morning_briefings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    briefing_text   TEXT NOT NULL,
    recipient_email TEXT,
    sent_at         TIMESTAMPTZ,
    language        TEXT DEFAULT 'English',
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_morning_briefings_company ON morning_briefings (company_id, created_at DESC);

-- ── Row Level Security on EVERY table ────────────────────────────────────────
-- The backend uses the service-role key (which bypasses RLS) and filters by
-- company in code. RLS is the safety net underneath: if the public "anon" key
-- is ever used against this database, no table is readable across companies.
-- Tables with a company_id get a company-isolation policy; tables without one
-- (e.g. subscription_plans) get RLS with no policy, i.e. service-role only.
DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN
        SELECT c.relname AS name,
               EXISTS (SELECT 1 FROM information_schema.columns col
                       WHERE col.table_schema = 'public' AND col.table_name = c.relname
                         AND col.column_name = 'company_id') AS has_company,
               EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.name);
        IF t.has_company AND NOT t.has_policy THEN
            EXECUTE format(
                'CREATE POLICY "company_isolation" ON public.%I FOR ALL '
                'USING (company_id = (auth.jwt() ->> ''company_id'')::uuid) '
                'WITH CHECK (company_id = (auth.jwt() ->> ''company_id'')::uuid)',
                t.name);
        END IF;
    END LOOP;
END $$;

-- `companies` holds every tenant's (encrypted) access tokens. It keeps RLS on
-- with NO policy at all: only the backend's service-role key can read it.
DROP POLICY IF EXISTS "company_isolation" ON companies;
