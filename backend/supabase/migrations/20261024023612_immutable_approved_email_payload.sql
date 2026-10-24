-- SecretaryAI V15: immutable per-execution email snapshot and atomic claim.
-- This stores the exact approved payload; it neither sends nor schedules mail.
CREATE TABLE agent_execution_email_payloads (
    company_id UUID NOT NULL,
    execution_id UUID NOT NULL,
    recipient TEXT NOT NULL CHECK(length(recipient) BETWEEN 3 AND 320),
    subject TEXT NOT NULL DEFAULT '' CHECK(length(subject)<=998),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 200000),
    payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(company_id,execution_id),
    FOREIGN KEY(company_id,execution_id)
      REFERENCES agent_executions(company_id,id)
);
ALTER TABLE agent_execution_email_payloads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON agent_execution_email_payloads
  FROM PUBLIC,anon,authenticated,service_role,secretary_executor;

CREATE FUNCTION reject_email_payload_rewrite() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $immutable$
BEGIN
  RAISE EXCEPTION 'approved email payload is immutable' USING ERRCODE='42501';
END
$immutable$;
CREATE TRIGGER trg_approved_email_payload_immutable
 BEFORE UPDATE OR DELETE ON agent_execution_email_payloads
 FOR EACH ROW EXECUTE FUNCTION reject_email_payload_rewrite();

CREATE OR REPLACE FUNCTION secretary_internal.approve_and_queue_email(
  p_company_id UUID,p_draft_id UUID,p_actor UUID,
  p_expected_content JSONB,p_idempotency_key TEXT,
  p_edited_content JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $approve$
DECLARE
  d drafts%ROWTYPE; v_execution_id UUID; next_status TEXT;
  payload JSONB; recipient TEXT; subject TEXT; body TEXT;
BEGIN
  IF p_company_id IS NULL OR p_draft_id IS NULL OR p_actor IS NULL OR
     p_expected_content IS NULL OR jsonb_typeof(p_expected_content)<>'object'
     OR p_idempotency_key IS NULL OR
     length(p_idempotency_key) NOT BETWEEN 1 AND 200 OR
     (p_edited_content IS NOT NULL AND jsonb_typeof(p_edited_content)<>'object') THEN
    RAISE EXCEPTION 'invalid approval request' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(
    SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
      AND is_active AND role IN ('owner','manager')
  ) THEN
    RAISE EXCEPTION 'approval actor is not authorized' USING ERRCODE='42501';
  END IF;
  SELECT * INTO d FROM drafts WHERE company_id=p_company_id
    AND id=p_draft_id FOR UPDATE;
  IF NOT FOUND OR d.action_type<>'email_reply' THEN
    RAISE EXCEPTION 'eligible email draft not found' USING ERRCODE='P0002';
  END IF;
  IF d.content IS DISTINCT FROM p_expected_content THEN
    RAISE EXCEPTION 'draft changed since policy evaluation' USING ERRCODE='40001';
  END IF;
  IF d.status IN ('approved','edited_and_approved') THEN
    SELECT id INTO v_execution_id FROM agent_executions
     WHERE company_id=p_company_id AND draft_id=p_draft_id
       AND idempotency_key=p_idempotency_key;
    IF NOT FOUND OR NOT EXISTS(
      SELECT 1 FROM agent_execution_email_payloads e
      WHERE e.company_id=p_company_id AND e.execution_id=v_execution_id
    ) THEN
      RAISE EXCEPTION 'approved draft lacks matching immutable payload'
        USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object('queued',FALSE,'already_processed',TRUE,
                              'execution_id',v_execution_id,'status',d.status);
  END IF;
  IF d.status<>'pending' THEN
    RAISE EXCEPTION 'draft is not pending' USING ERRCODE='42501';
  END IF;
  payload:=coalesce(p_edited_content,d.content);
  recipient:=payload->>'to';
  subject:=coalesce(payload->>'subject','');
  body:=payload->>'body';
  IF recipient IS NULL OR length(btrim(recipient)) NOT BETWEEN 3 AND 320
     OR body IS NULL OR length(body) NOT BETWEEN 1 AND 200000
     OR length(subject)>998 THEN
    RAISE EXCEPTION 'invalid approved email recipient or body'
      USING ERRCODE='22023';
  END IF;
  next_status:=CASE WHEN p_edited_content IS NULL
    THEN 'approved' ELSE 'edited_and_approved' END;
  UPDATE drafts SET status=next_status,reviewed_by=p_actor,reviewed_at=now(),
    content=payload WHERE company_id=p_company_id AND id=p_draft_id;
  INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
    VALUES(p_company_id,p_draft_id,p_idempotency_key,'email_reply')
    RETURNING id INTO v_execution_id;
  INSERT INTO agent_execution_email_payloads(
    company_id,execution_id,recipient,subject,body,payload_sha256
  ) VALUES(
    p_company_id,v_execution_id,recipient,subject,body,
    encode(digest(convert_to(payload::TEXT,'UTF8'),'sha256'),'hex')
  );
  RETURN jsonb_build_object('queued',TRUE,'already_processed',FALSE,
                            'execution_id',v_execution_id,'status',next_status);
END
$approve$;

CREATE FUNCTION secretary_internal.claim_approved_email(
  p_company_id UUID,p_execution_id UUID,p_worker_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $claim$
DECLARE p agent_execution_email_payloads%ROWTYPE; result JSONB;
BEGIN
  SELECT * INTO p FROM agent_execution_email_payloads
    WHERE company_id=p_company_id AND execution_id=p_execution_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable email payload unavailable' USING ERRCODE='P0002';
  END IF;
  result:=secretary_internal.claim_agent_execution(
    p_company_id,p_execution_id,p_worker_id);
  IF result->>'claimed'<>'true' THEN RETURN result; END IF;
  RETURN result || jsonb_build_object(
    'recipient',p.recipient,'subject',p.subject,
    'body',p.body,'payload_sha256',p.payload_sha256
  );
END
$claim$;
REVOKE ALL ON FUNCTION
 secretary_internal.claim_approved_email(UUID,UUID,TEXT)
 FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION
 secretary_internal.claim_approved_email(UUID,UUID,TEXT)
 TO secretary_executor;
