-- SecretaryAI V11: atomic execution claims, intentionally not dispatched.
-- Requires V10. Functions reside outside Supabase's exposed public schema.
-- They are executable only by the trusted service_role, not end users.
CREATE SCHEMA IF NOT EXISTS secretary_internal;
REVOKE ALL ON SCHEMA secretary_internal FROM PUBLIC,anon,authenticated;
GRANT USAGE ON SCHEMA secretary_internal TO service_role;

ALTER TABLE agent_executions
  ADD COLUMN claim_token UUID,
  ADD COLUMN claimed_at TIMESTAMPTZ,
  ADD COLUMN worker_id TEXT;
ALTER TABLE agent_executions ADD CONSTRAINT agent_execution_claim_pair
  CHECK ((claim_token IS NULL AND claimed_at IS NULL AND worker_id IS NULL)
      OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL
          AND worker_id IS NOT NULL AND length(worker_id) BETWEEN 1 AND 120));

CREATE FUNCTION secretary_internal.enqueue_agent_execution(
  p_company_id UUID,p_draft_id UUID,p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $enqueue$
DECLARE d drafts%ROWTYPE; e agent_executions%ROWTYPE;
BEGIN
  IF p_company_id IS NULL OR p_draft_id IS NULL OR
     p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'invalid execution identity' USING ERRCODE='22023';
  END IF;
  SELECT * INTO d FROM drafts
    WHERE company_id=p_company_id AND id=p_draft_id FOR UPDATE;
  IF NOT FOUND OR d.status NOT IN ('approved','edited_and_approved') THEN
    RAISE EXCEPTION 'approved company draft required' USING ERRCODE='42501';
  END IF;
  INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
    VALUES(p_company_id,p_draft_id,p_idempotency_key,d.action_type)
    ON CONFLICT DO NOTHING;
  SELECT * INTO e FROM agent_executions
    WHERE company_id=p_company_id AND draft_id=p_draft_id;
  IF NOT FOUND OR e.idempotency_key<>p_idempotency_key
     OR e.action_type<>d.action_type THEN
    RAISE EXCEPTION 'conflicting execution idempotency key' USING ERRCODE='23505';
  END IF;
  RETURN jsonb_build_object('execution_id',e.id,'state',e.state);
END
$enqueue$;

CREATE FUNCTION secretary_internal.claim_agent_execution(
  p_company_id UUID,p_execution_id UUID,p_worker_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $claim$
DECLARE e agent_executions%ROWTYPE; token UUID;
BEGIN
  IF p_company_id IS NULL OR p_execution_id IS NULL OR
     p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 120 THEN
    RAISE EXCEPTION 'invalid worker claim' USING ERRCODE='22023';
  END IF;
  token:=gen_random_uuid();
  UPDATE agent_executions SET
    state='running',attempt=attempt+1,claim_token=token,
    claimed_at=now(),worker_id=p_worker_id
  WHERE company_id=p_company_id AND id=p_execution_id AND state='queued'
  RETURNING * INTO e;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('claimed',FALSE);
  END IF;
  RETURN jsonb_build_object('claimed',TRUE,'execution_id',e.id,
                           'claim_token',e.claim_token,'attempt',e.attempt);
END
$claim$;

CREATE FUNCTION secretary_internal.finish_agent_execution(
  p_company_id UUID,p_execution_id UUID,p_claim_token UUID,
  p_outcome TEXT,p_external_reference TEXT DEFAULT NULL,
  p_error_code TEXT DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $finish$
DECLARE e agent_executions%ROWTYPE;
BEGIN
  IF p_company_id IS NULL OR p_execution_id IS NULL OR p_claim_token IS NULL OR
     p_outcome IS NULL OR
     p_outcome NOT IN ('completed','failed','needs_reconciliation') THEN
    RAISE EXCEPTION 'invalid execution outcome' USING ERRCODE='22023';
  END IF;
  UPDATE agent_executions
    SET state=p_outcome,external_reference=p_external_reference,
        last_error_code=p_error_code
  WHERE company_id=p_company_id AND id=p_execution_id
    AND state='running' AND claim_token=p_claim_token
  RETURNING * INTO e;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('finalized',FALSE);
  END IF;
  RETURN jsonb_build_object('finalized',TRUE,'state',e.state);
END
$finish$;

REVOKE ALL ON FUNCTION
  secretary_internal.enqueue_agent_execution(UUID,UUID,TEXT),
  secretary_internal.claim_agent_execution(UUID,UUID,TEXT),
  secretary_internal.finish_agent_execution(UUID,UUID,UUID,TEXT,TEXT,TEXT)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION
  secretary_internal.enqueue_agent_execution(UUID,UUID,TEXT),
  secretary_internal.claim_agent_execution(UUID,UUID,TEXT),
  secretary_internal.finish_agent_execution(UUID,UUID,UUID,TEXT,TEXT,TEXT)
  TO service_role;
