-- SecretaryAI V14: atomic human approval and durable enqueue, no dispatch.
-- Restricted to email_reply drafts while the new approval route is dark.
CREATE FUNCTION secretary_internal.approve_and_queue_email(
  p_company_id UUID,p_draft_id UUID,p_actor UUID,
  p_expected_content JSONB,p_idempotency_key TEXT,
  p_edited_content JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $approve$
DECLARE d drafts%ROWTYPE; execution_id UUID; next_status TEXT;
BEGIN
  IF p_company_id IS NULL OR p_draft_id IS NULL OR p_actor IS NULL OR
     p_expected_content IS NULL OR jsonb_typeof(p_expected_content)<>'object'
     OR p_idempotency_key IS NULL OR
     length(p_idempotency_key) NOT BETWEEN 1 AND 200 OR
     (p_edited_content IS NOT NULL AND jsonb_typeof(p_edited_content)<>'object') THEN
    RAISE EXCEPTION 'invalid approval request' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (
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
    RAISE EXCEPTION 'draft changed since policy evaluation'
      USING ERRCODE='40001';
  END IF;
  IF d.status IN ('approved','edited_and_approved') THEN
    SELECT id INTO execution_id FROM agent_executions
      WHERE company_id=p_company_id AND draft_id=p_draft_id
        AND idempotency_key=p_idempotency_key;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'approved draft has no matching execution'
        USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object('queued',FALSE,'already_processed',TRUE,
                              'execution_id',execution_id,'status',d.status);
  END IF;
  IF d.status<>'pending' THEN
    RAISE EXCEPTION 'draft is not pending' USING ERRCODE='42501';
  END IF;
  next_status:=CASE WHEN p_edited_content IS NULL
    THEN 'approved' ELSE 'edited_and_approved' END;
  UPDATE drafts SET status=next_status,reviewed_by=p_actor,reviewed_at=now(),
    content=coalesce(p_edited_content,d.content)
  WHERE company_id=p_company_id AND id=p_draft_id;
  INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
    VALUES(p_company_id,p_draft_id,p_idempotency_key,'email_reply')
    RETURNING id INTO execution_id;
  RETURN jsonb_build_object('queued',TRUE,'already_processed',FALSE,
                            'execution_id',execution_id,'status',next_status);
END
$approve$;
REVOKE ALL ON FUNCTION
  secretary_internal.approve_and_queue_email(UUID,UUID,UUID,JSONB,TEXT,JSONB)
  FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION
  secretary_internal.approve_and_queue_email(UUID,UUID,UUID,JSONB,TEXT,JSONB)
  TO secretary_executor;
