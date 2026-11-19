-- SecretaryAI V10: dormant durable execution ledger.
-- No worker or API may dispatch from this schema. A later reviewed release
-- must add an atomic claim/outbox protocol before enabling side effects.
ALTER TABLE drafts ADD CONSTRAINT drafts_company_id_id_unique UNIQUE(company_id,id);

CREATE TABLE agent_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    draft_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 200),
    action_type TEXT NOT NULL CHECK(length(action_type) BETWEEN 1 AND 150),
    state TEXT NOT NULL DEFAULT 'queued'
      CHECK(state IN ('queued','running','completed','failed','needs_reconciliation')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    external_reference TEXT,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id), UNIQUE(company_id,draft_id),
    UNIQUE(company_id,idempotency_key),
    FOREIGN KEY(company_id,draft_id) REFERENCES drafts(company_id,id)
);
CREATE INDEX idx_agent_executions_tenant_state
    ON agent_executions(company_id,state,created_at);
ALTER TABLE agent_executions ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_execution_tenant_read ON agent_executions
    FOR SELECT USING(company_id=(auth.jwt()->>'company_id')::UUID);

-- In the dormant phase even authenticated users and the API's service role
-- cannot INSERT/UPDATE/DELETE these records through PostgREST.
REVOKE ALL ON agent_executions FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON agent_executions TO authenticated,service_role;

CREATE FUNCTION guard_agent_execution_transition() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $guard$
DECLARE
    v_draft_status TEXT;
    v_action_type TEXT;
BEGIN
    IF TG_OP='INSERT' THEN
        SELECT status,action_type INTO v_draft_status,v_action_type FROM drafts
          WHERE company_id=NEW.company_id AND id=NEW.draft_id;
        IF v_draft_status NOT IN ('approved','edited_and_approved')
           OR NEW.state <> 'queued' OR NEW.attempt <> 0
           OR v_action_type IS DISTINCT FROM NEW.action_type
           OR NEW.external_reference IS NOT NULL THEN
            RAISE EXCEPTION 'execution must start queued for an approved draft'
              USING ERRCODE='23514';
        END IF;
    ELSIF TG_OP='UPDATE' THEN
        IF NEW.id<>OLD.id OR NEW.company_id<>OLD.company_id
           OR NEW.draft_id<>OLD.draft_id
           OR NEW.idempotency_key<>OLD.idempotency_key
           OR NEW.action_type<>OLD.action_type OR NEW.created_at<>OLD.created_at THEN
            RAISE EXCEPTION 'execution identity is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.state=OLD.state THEN
            RAISE EXCEPTION 'execution updates require state transition'
              USING ERRCODE='23514';
        END IF;
        IF NOT (
            (OLD.state='queued' AND NEW.state IN ('running','failed')) OR
            (OLD.state='running' AND NEW.state IN
                ('completed','failed','needs_reconciliation')) OR
            (OLD.state='failed' AND NEW.state='queued') OR
            (OLD.state='needs_reconciliation' AND NEW.state IN ('completed','failed'))
        ) THEN
            RAISE EXCEPTION 'invalid execution state transition' USING ERRCODE='23514';
        END IF;
        IF (OLD.state='queued' AND NEW.state='running'
            AND NEW.attempt<>OLD.attempt+1) OR
           (NOT (OLD.state='queued' AND NEW.state='running')
            AND NEW.attempt<>OLD.attempt) THEN
            RAISE EXCEPTION 'invalid attempt counter' USING ERRCODE='23514';
        END IF;
        -- Requeuing or reconciling uncertain effects requires future explicit
        -- privileged RPCs; never authorize these by generic table writes.
        IF OLD.state IN ('failed','needs_reconciliation') THEN
            RAISE EXCEPTION 'requeue and reconciliation require reviewed RPC'
              USING ERRCODE='42501';
        END IF;
        NEW.updated_at:=now();
    ELSE
        RAISE EXCEPTION 'execution history cannot be deleted'
          USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END
$guard$;
CREATE TRIGGER trg_agent_executions_guard
  BEFORE INSERT OR UPDATE OR DELETE ON agent_executions
  FOR EACH ROW EXECUTE FUNCTION guard_agent_execution_transition();
