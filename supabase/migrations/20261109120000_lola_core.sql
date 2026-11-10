-- Lion BI / Lola — Workstream 1 core tables (additive; touches no existing rows).
--
-- Org hierarchy + clearances, expiring delegations, isolated memory and durable
-- action receipts. Every table is tenant-keyed and reachable ONLY through the
-- backend (service_role). Browser keys (anon/authenticated) get nothing: Lola's
-- record-level rules live in the API, and direct table access would bypass them.
-- No row in this migration can cause an external side effect.

-- Same-tenant foreign keys need a composite key on users.
DO $users_key$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_company_id_id_unique'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_company_id_id_unique UNIQUE (company_id, id);
    END IF;
END
$users_key$;

-- ── Org profile ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lola_org_members (
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL,
    org_level           TEXT NOT NULL DEFAULT 'employee'
                        CHECK (org_level IN ('executive','department_manager','employee')),
    department          TEXT NOT NULL DEFAULT 'general'
                        CHECK (department IN ('executive','sales','finance','operations','purchasing',
                                              'hr','compliance','general')),
    manager_user_id     UUID,
    territories         TEXT[] NOT NULL DEFAULT '{}',
    financial_clearance BOOLEAN NOT NULL DEFAULT FALSE,
    hr_clearance        BOOLEAN NOT NULL DEFAULT FALSE,
    action_limit        NUMERIC(14,2) CHECK (action_limit IS NULL OR action_limit >= 0),
    updated_by          UUID,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, user_id),
    CHECK (manager_user_id IS NULL OR manager_user_id <> user_id),
    FOREIGN KEY (company_id, user_id) REFERENCES users(company_id, id) ON DELETE CASCADE,
    FOREIGN KEY (company_id, manager_user_id) REFERENCES users(company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_lola_org_members_manager
    ON lola_org_members(company_id, manager_user_id);

-- ── Delegations (always expiring, never hard-deleted) ───────────────────────
CREATE TABLE IF NOT EXISTS lola_delegations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    grantor_user_id  UUID NOT NULL,
    grantee_user_id  UUID NOT NULL,
    resource_types   TEXT[] NOT NULL CHECK (
        cardinality(resource_types) BETWEEN 1 AND 8
        AND resource_types <@ ARRAY['customer_account','sales','receivables','inventory',
                                    'inventory_cost','purchasing','financials']::TEXT[]
    ),
    account_ids      TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(account_ids) <= 200),
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 300),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ,
    CHECK (grantor_user_id <> grantee_user_id),
    CHECK (expires_at > created_at AND expires_at <= created_at + INTERVAL '90 days'),
    FOREIGN KEY (company_id, grantor_user_id) REFERENCES users(company_id, id) ON DELETE CASCADE,
    FOREIGN KEY (company_id, grantee_user_id) REFERENCES users(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lola_delegations_grantee
    ON lola_delegations(company_id, grantee_user_id) WHERE revoked_at IS NULL;

-- ── Memory ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lola_memories (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id             UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    owner_user_id          UUID NOT NULL,
    scope                  TEXT NOT NULL CHECK (scope IN ('user','team','company')),
    kind                   TEXT NOT NULL CHECK (kind IN ('preference','business_context','decision',
                                                         'commitment','workflow')),
    sensitivity            TEXT NOT NULL CHECK (sensitivity IN ('public','internal','confidential',
                                                                'restricted')),
    department             TEXT,
    content                TEXT NOT NULL CHECK (length(btrim(content)) BETWEEN 1 AND 1000),
    entity_type            TEXT,
    entity_id              TEXT,
    source_conversation_id TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at             TIMESTAMPTZ,
    deleted_at             TIMESTAMPTZ,
    -- Confidential/restricted memory can never be shared beyond its owner.
    CHECK (sensitivity IN ('public','internal') OR scope = 'user'),
    FOREIGN KEY (company_id, owner_user_id) REFERENCES users(company_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lola_memories_recall
    ON lola_memories(company_id, created_at DESC) WHERE deleted_at IS NULL;

-- ── Action receipts ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lola_action_receipts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id         UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    proposal_id        UUID NOT NULL,
    action_type        TEXT NOT NULL CHECK (length(action_type) BETWEEN 1 AND 150),
    risk_tier          TEXT NOT NULL CHECK (risk_tier IN ('read_only','draft','approval_required',
                                                          'routine_automation','high_risk')),
    status             TEXT NOT NULL CHECK (status IN ('draft','pending_approval','approved','executing',
                                                       'completed','failed','rejected','denied',
                                                       'needs_reconciliation')),
    idempotency_key    TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    requested_by       UUID NOT NULL,
    draft_id           UUID,
    approved_by        UUID,
    execution_id       UUID,
    external_reference TEXT,
    verified           BOOLEAN NOT NULL DEFAULT FALSE,
    error_code         TEXT,
    proposal           JSONB NOT NULL,
    history            JSONB NOT NULL DEFAULT '[]'::JSONB CHECK (jsonb_typeof(history) = 'array'),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, idempotency_key),
    UNIQUE (company_id, id),
    -- "Completed" means verified with a provider/executor reference. Never otherwise.
    CHECK (status <> 'completed' OR (verified AND external_reference IS NOT NULL)),
    CHECK (NOT verified OR status = 'completed'),
    FOREIGN KEY (company_id, requested_by) REFERENCES users(company_id, id)
);
CREATE INDEX IF NOT EXISTS idx_lola_receipts_requester
    ON lola_action_receipts(company_id, requested_by, created_at DESC);

CREATE OR REPLACE FUNCTION guard_lola_action_receipt() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $guard$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Lola receipts are permanent audit records' USING ERRCODE = '42501';
    END IF;
    IF NEW.id <> OLD.id OR NEW.company_id <> OLD.company_id
       OR NEW.idempotency_key <> OLD.idempotency_key OR NEW.action_type <> OLD.action_type
       OR NEW.requested_by <> OLD.requested_by OR NEW.proposal_id <> OLD.proposal_id
       OR NEW.risk_tier <> OLD.risk_tier OR NEW.proposal <> OLD.proposal
       OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'Lola receipt identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('completed','rejected','denied') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'Lola receipt is final' USING ERRCODE = '23514';
    END IF;
    -- Existing entries must be kept exactly; only appending is allowed.
    IF jsonb_array_length(NEW.history) < jsonb_array_length(OLD.history)
       OR COALESCE((
            SELECT jsonb_agg(e ORDER BY i)
              FROM jsonb_array_elements(NEW.history) WITH ORDINALITY AS t(e, i)
             WHERE i <= jsonb_array_length(OLD.history)
          ), '[]'::JSONB) IS DISTINCT FROM OLD.history THEN
        RAISE EXCEPTION 'Lola receipt history is append-only' USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$guard$;
DROP TRIGGER IF EXISTS trg_guard_lola_action_receipt ON lola_action_receipts;
CREATE TRIGGER trg_guard_lola_action_receipt
    BEFORE UPDATE OR DELETE ON lola_action_receipts
    FOR EACH ROW EXECUTE FUNCTION guard_lola_action_receipt();

-- ── Access: backend only ────────────────────────────────────────────────────
ALTER TABLE lola_org_members     ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_delegations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_memories        ENABLE ROW LEVEL SECURITY;
ALTER TABLE lola_action_receipts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON lola_org_members, lola_delegations, lola_memories, lola_action_receipts
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON lola_org_members, lola_delegations, lola_memories, lola_action_receipts
    FROM service_role;
-- Org profiles are configuration: full edit by the backend.
GRANT SELECT, INSERT, UPDATE, DELETE ON lola_org_members TO service_role;
-- Delegations, memories and receipts are history: no hard deletes.
GRANT SELECT, INSERT, UPDATE ON lola_delegations, lola_memories, lola_action_receipts TO service_role;
