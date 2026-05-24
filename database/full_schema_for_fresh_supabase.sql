-- ============================================================
-- Migration: 20240101000000_initial_schema.sql
-- ============================================================
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

-- ============================================================
-- Migration: 20240601000000_v2_additions.sql
-- ============================================================
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

-- ============================================================
-- Migration: 20240901000000_v3_integrations.sql
-- ============================================================
-- SecretaryAI V3 Integration Columns
-- Run AFTER schema_v2_additions.sql.
-- Adds timestamp tracking for OAuth connections.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS qbo_connected_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS google_connected_at    TIMESTAMPTZ;

-- ============================================================
-- Migration: 20250322000000_draft_expiry.sql
-- ============================================================
-- Migration: add expires_at to drafts table for approval expiry (Tier 2-13)
-- Drafts pending beyond this timestamp are auto-archived by the nightly task.
-- Default: 30 days from creation.

ALTER TABLE drafts
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ
    GENERATED ALWAYS AS (created_at + INTERVAL '30 days') STORED;

-- Index to speed up the nightly expiry query
CREATE INDEX IF NOT EXISTS idx_drafts_expires_at
  ON drafts (expires_at)
  WHERE status = 'pending';

-- ============================================================
-- Migration: 20250322000001_ingested_files_hash.sql
-- ============================================================
-- Migration: add file_hash to ingested_files for deduplication (Tier 2-11)
-- SHA-256 of filename + row_count + first-row fingerprint.
-- Unique per company to prevent the same file being ingested twice.

ALTER TABLE ingested_files
  ADD COLUMN IF NOT EXISTS file_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_files_hash
  ON ingested_files (company_id, file_hash)
  WHERE file_hash IS NOT NULL;

-- ============================================================
-- Migration: 20250325000000_report_snapshots.sql
-- ============================================================
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

-- ============================================================
-- Migration: 20250325000001_memory.sql
-- ============================================================
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
CREATE POLICY "account_memory_company_isolation" ON account_memory
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

ALTER TABLE conversation_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY "conv_summaries_company_isolation" ON conversation_summaries
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

-- ============================================================
-- Migration: 20250325000002_feed_events.sql
-- ============================================================
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
CREATE POLICY "feed_events_company_isolation" ON feed_events
    FOR ALL USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

-- ============================================================
-- Migration: 20250325000003_workflows.sql
-- ============================================================
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

-- ============================================================
-- Migration: 20250325000004_connector_fields.sql
-- ============================================================
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

-- ============================================================
-- Migration: 20250325000005_conductor_end_user.sql
-- ============================================================
-- Store the Conductor EndUser ID so we can route QB Desktop API calls
-- and avoid creating duplicate EndUsers on retries.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS conductor_end_user_id TEXT;

COMMENT ON COLUMN companies.conductor_end_user_id IS
  'Conductor (conductor.is) EndUser ID — links this company to their QB Desktop Web Connector session.';

-- ============================================================
-- Migration: 20260524000000_feature_factory.sql
-- (FK CONFIRM: lines already pointed at companies/users)
-- ============================================================
-- =============================================================================
-- SecretaryAI · Feature Factory — Database Migration 001
-- =============================================================================
-- PURPOSE
--   Adds the "self-extending feature" subsystem. Lets a logged-in user describe
--   a feature in plain English; the system safely generates, reviews, and runs
--   it. This migration is PURELY ADDITIVE — it only CREATEs new objects.
--   It never alters or drops anything you already have.
--
-- NAMING
--   Every object is prefixed `ff_` (feature factory) so it can never collide
--   with an existing table.
--
-- ⚠️ TWO FOREIGN KEYS TO CONFIRM (search this file for "CONFIRM:")
--   This module references your existing tenant and user tables. We do NOT
--   assume their names. Wherever you see `-- CONFIRM:` swap in your real
--   table/column. If your schema already uses `tenant_id` / `users(id)`,
--   you can run this file unchanged.
--
-- HOW TO RUN
--   Supabase: paste into the SQL editor and Run.
--   psql:     psql "$DATABASE_URL" -f 001_feature_factory.sql
-- =============================================================================

BEGIN;

-- Needed for gen_random_uuid(). Safe to run if already enabled.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- ENUMS  (created defensively so re-running the file does not error)
-- -----------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE ff_feature_status AS ENUM (
    'draft',                       -- interpreter produced a spec, not yet validated
    'pending_capability_approval', -- waiting for a human to approve what it can touch
    'dry_run_ready',               -- approved capabilities; ready to preview safely
    'observe',                     -- runs on schedule but only LOGS what it would do
    'active',                      -- runs for real (commit actions still need approval)
    'disabled',                    -- switched off by a human / kill switch
    'failed',                      -- auto-disabled by the circuit breaker
    'rejected'                     -- a human declined it
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE ff_run_mode AS ENUM ('dry_run', 'observe', 'act');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE ff_run_status AS ENUM ('success', 'failed', 'blocked', 'awaiting_approval');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE ff_approval_status AS ENUM ('pending', 'approved', 'rejected', 'expired');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -----------------------------------------------------------------------------
-- 1. ff_features — the Feature Registry (one row per generated feature)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ff_features (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- CONFIRM: point this at your existing tenant/org table.
  -- e.g. REFERENCES organizations(id). Kept as a plain UUID + index if your
  -- tenant key lives elsewhere; uncomment the REFERENCES line to enforce it.
  tenant_id                UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

  -- CONFIRM: point this at your existing users table.
  created_by               UUID NOT NULL REFERENCES users(id),

  name                     TEXT NOT NULL,
  description              TEXT NOT NULL,          -- plain-English summary the user approves
  request_text             TEXT NOT NULL,          -- the user's original words
  tier                     SMALLINT NOT NULL DEFAULT 1 CHECK (tier IN (1, 2, 3)),
  status                   ff_feature_status NOT NULL DEFAULT 'draft',

  spec                     JSONB NOT NULL,         -- the executable workflow definition (Tier 1)
  declared_capabilities    JSONB NOT NULL DEFAULT '[]'::jsonb, -- the capability "contract"

  trigger_kind             TEXT NOT NULL DEFAULT 'manual', -- manual | schedule | event
  schedule_cron            TEXT,                   -- when trigger_kind = 'schedule'

  -- Progressive trust (Layer 6)
  observe_runs_required    INT NOT NULL DEFAULT 5,
  successful_observe_runs  INT NOT NULL DEFAULT 0,

  -- Circuit breaker (Layer 7)
  consecutive_failures     INT NOT NULL DEFAULT 0,
  failure_threshold        INT NOT NULL DEFAULT 3,

  version                  INT NOT NULL DEFAULT 1,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ff_features_tenant   ON ff_features (tenant_id);
CREATE INDEX IF NOT EXISTS ix_ff_features_status   ON ff_features (tenant_id, status);

-- -----------------------------------------------------------------------------
-- 2. ff_capability_grants — exactly what a human approved this feature to touch
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ff_capability_grants (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  feature_id      UUID NOT NULL REFERENCES ff_features(id) ON DELETE CASCADE,
  tenant_id       UUID NOT NULL,

  capability_key  TEXT NOT NULL,           -- e.g. 'invoices.read', 'flags.write'
  resource        TEXT NOT NULL,           -- e.g. 'invoices'
  scope           TEXT NOT NULL CHECK (scope IN ('read', 'draft', 'commit', 'destructive')),

  approved_by     UUID NOT NULL REFERENCES users(id),
  approved_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ff_grants_feature ON ff_capability_grants (feature_id);

-- -----------------------------------------------------------------------------
-- 3. ff_feature_runs — every execution (dry-run, observe, or act) is recorded
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ff_feature_runs (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  feature_id        UUID NOT NULL REFERENCES ff_features(id) ON DELETE CASCADE,
  tenant_id         UUID NOT NULL,

  mode              ff_run_mode NOT NULL,
  status            ff_run_status NOT NULL,
  trigger           TEXT NOT NULL,         -- 'manual' | 'schedule' | 'preview'
  correlation_id    UUID NOT NULL,         -- ties together logs/jobs for one run

  planned_actions   JSONB NOT NULL DEFAULT '[]'::jsonb, -- what it intended to do
  executed_actions  JSONB NOT NULL DEFAULT '[]'::jsonb, -- what it actually did (act mode)
  result_summary    TEXT,
  error             TEXT,

  started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_ff_runs_feature ON ff_feature_runs (feature_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_ff_runs_corr    ON ff_feature_runs (correlation_id);

-- -----------------------------------------------------------------------------
-- 4. ff_approvals — human sign-off for risky (commit/destructive) actions
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ff_approvals (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  feature_id       UUID NOT NULL REFERENCES ff_features(id) ON DELETE CASCADE,
  run_id           UUID REFERENCES ff_feature_runs(id) ON DELETE SET NULL,
  tenant_id        UUID NOT NULL,

  action           JSONB NOT NULL,         -- the specific action awaiting approval
  action_class     TEXT NOT NULL CHECK (action_class IN ('commit', 'destructive')),
  status           ff_approval_status NOT NULL DEFAULT 'pending',

  before_snapshot  JSONB,                  -- state before (where relevant)
  after_snapshot   JSONB,                  -- state after approval+execution

  requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by       UUID REFERENCES users(id),
  decided_at       TIMESTAMPTZ,
  expires_at       TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '7 days')
);

CREATE INDEX IF NOT EXISTS ix_ff_approvals_pending
  ON ff_approvals (tenant_id, status) WHERE status = 'pending';

-- -----------------------------------------------------------------------------
-- 5. ff_audit_log — append-only, hash-chained record of privileged events
--    (Layer 8). Each row hashes the previous row's hash, so any tampering
--    breaks the chain and is detectable.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ff_audit_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL,
  feature_id    UUID,                      -- nullable: some events are tenant-wide
  actor_id      UUID,                      -- the human or NULL for system
  event_type    TEXT NOT NULL,             -- e.g. 'feature.created', 'capabilities.approved'
  detail        JSONB NOT NULL DEFAULT '{}'::jsonb,

  prev_hash     TEXT,                      -- hash of the previous audit row for this tenant
  hash          TEXT NOT NULL,             -- sha256(prev_hash + canonical(this row))

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ff_audit_tenant ON ff_audit_log (tenant_id, created_at DESC);

-- Block UPDATE/DELETE on the audit log at the database level. The log is
-- append-only; even a bug in the app cannot rewrite history.
CREATE OR REPLACE FUNCTION ff_audit_no_mutate() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'ff_audit_log is append-only; % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ff_audit_no_update ON ff_audit_log;
CREATE TRIGGER trg_ff_audit_no_update
  BEFORE UPDATE OR DELETE ON ff_audit_log
  FOR EACH ROW EXECUTE FUNCTION ff_audit_no_mutate();

-- -----------------------------------------------------------------------------
-- 6. ff_tenant_settings — the per-tenant kill switch & global toggles (Layer 7)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ff_tenant_settings (
  tenant_id              UUID PRIMARY KEY,
  feature_factory_enabled BOOLEAN NOT NULL DEFAULT TRUE, -- master kill switch
  max_features           INT NOT NULL DEFAULT 50,        -- abuse / rate guard
  allow_tier_2           BOOLEAN NOT NULL DEFAULT FALSE, -- sandboxed code off by default
  allow_tier_3           BOOLEAN NOT NULL DEFAULT FALSE, -- full-gen off by default
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- updated_at convenience trigger
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ff_touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ff_features_touch ON ff_features;
CREATE TRIGGER trg_ff_features_touch
  BEFORE UPDATE ON ff_features
  FOR EACH ROW EXECUTE FUNCTION ff_touch_updated_at();

COMMIT;

-- =============================================================================
-- OPTIONAL: Row-Level Security (RLS).
-- If your app already enforces tenant isolation in code, you can skip this.
-- If you use Supabase RLS, enable and add policies that match your auth model.
-- Left commented so it does not conflict with your existing policy strategy.
-- =============================================================================
-- ALTER TABLE ff_features          ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE ff_capability_grants ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE ff_feature_runs      ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE ff_approvals         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE ff_audit_log         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE ff_tenant_settings   ENABLE ROW LEVEL SECURITY;
