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
  tenant_id                UUID NOT NULL, -- CONFIRM: REFERENCES tenants(id) ON DELETE CASCADE

  -- CONFIRM: point this at your existing users table.
  created_by               UUID NOT NULL, -- CONFIRM: REFERENCES users(id)

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

  approved_by     UUID NOT NULL,           -- CONFIRM: REFERENCES users(id)
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
  decided_by       UUID,                   -- CONFIRM: REFERENCES users(id)
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
