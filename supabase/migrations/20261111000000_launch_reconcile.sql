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
    execution_status IS NULL OR execution_status IN ('executed', 'manual_required', 'failed'));
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
