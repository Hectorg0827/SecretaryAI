-- SecretaryAI V17: permit only a single allowlisted staging mailbox claim.
-- Rejection happens BEFORE the queue transitions to running.
CREATE FUNCTION secretary_internal.claim_staging_email(
  p_company_id UUID,p_execution_id UUID,p_worker_id TEXT,
  p_allowed_recipient TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $claim$
DECLARE p agent_execution_email_payloads%ROWTYPE;
BEGIN
  IF p_allowed_recipient IS NULL OR
     length(p_allowed_recipient) NOT BETWEEN 3 AND 320 OR
     p_allowed_recipient !~ '^[^@,;[:space:]]+@[^@,;[:space:]]+\\.[^@,;[:space:]]+$'
  THEN
    RAISE EXCEPTION 'one explicit staging recipient is required'
      USING ERRCODE='22023';
  END IF;
  SELECT * INTO p FROM agent_execution_email_payloads
   WHERE company_id=p_company_id AND execution_id=p_execution_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'approved email snapshot not found' USING ERRCODE='P0002';
  END IF;
  IF lower(p.recipient)<>lower(p_allowed_recipient) THEN
    RAISE EXCEPTION 'recipient does not match staging allowlist'
      USING ERRCODE='42501';
  END IF;
  RETURN secretary_internal.claim_approved_email(
    p_company_id,p_execution_id,p_worker_id
  );
END
$claim$;
REVOKE ALL ON FUNCTION
 secretary_internal.claim_staging_email(UUID,UUID,TEXT,TEXT)
 FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION
 secretary_internal.claim_staging_email(UUID,UUID,TEXT,TEXT)
 TO secretary_executor;
