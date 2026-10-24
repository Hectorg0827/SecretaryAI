-- V10 execution ledger: real PostgreSQL constraints, rollback-only fixtures.
\set ON_ERROR_STOP on
BEGIN;
DO $ledger$
DECLARE
  a UUID; b UUID; draft_a UUID; draft_b UUID; execution_a UUID;
  denied BOOLEAN; count_a INTEGER;
BEGIN
  INSERT INTO companies(name) VALUES('Execution A') RETURNING id INTO a;
  INSERT INTO companies(name) VALUES('Execution B') RETURNING id INTO b;
  INSERT INTO drafts(company_id,created_by,action_type,content,status)
    VALUES(a,'ai','draft_customer_email','{}'::jsonb,'approved')
    RETURNING id INTO draft_a;
  INSERT INTO drafts(company_id,created_by,action_type,content,status)
    VALUES(b,'ai','draft_customer_email','{}'::jsonb,'approved')
    RETURNING id INTO draft_b;
  INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
    VALUES(a,draft_a,'same-business-key','draft_customer_email')
    RETURNING id INTO execution_a;
  -- A different company may use the same idempotency key.
  INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
    VALUES(b,draft_b,'same-business-key','draft_customer_email');

  denied:=FALSE;
  BEGIN
    INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
      VALUES(a,draft_a,'another-key','draft_customer_email');
  EXCEPTION WHEN unique_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'duplicate draft ledger allowed'; END IF;

  denied:=FALSE;
  BEGIN
    INSERT INTO agent_executions(company_id,draft_id,idempotency_key,action_type)
      VALUES(a,draft_b,'cross-company-key','draft_customer_email');
  EXCEPTION WHEN foreign_key_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'cross-tenant draft reference allowed'; END IF;

  denied:=FALSE;
  BEGIN
    UPDATE agent_executions SET state='completed' WHERE id=execution_a;
  EXCEPTION WHEN check_violation THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'queued to completed skipped running'; END IF;

  UPDATE agent_executions SET state='running',attempt=1 WHERE id=execution_a;
  UPDATE agent_executions SET state='needs_reconciliation'
    WHERE id=execution_a;
  denied:=FALSE;
  BEGIN
    UPDATE agent_executions SET state='queued' WHERE id=execution_a;
  EXCEPTION WHEN check_violation OR insufficient_privilege THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'uncertain send was requeued'; END IF;

  denied:=FALSE;
  BEGIN
    DELETE FROM agent_executions WHERE id=execution_a;
  EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'ledger history was deleted'; END IF;

  IF has_table_privilege('authenticated','agent_executions','INSERT')
     OR has_table_privilege('authenticated','agent_executions','UPDATE')
     OR has_table_privilege('service_role','agent_executions','UPDATE') THEN
     RAISE EXCEPTION 'unreviewed runtime writer has ledger privilege';
  END IF;

  SELECT count(*) INTO count_a FROM agent_executions WHERE company_id=a;
  IF count_a<>1 THEN RAISE EXCEPTION 'ledger duplicate appeared'; END IF;
END
$ledger$;
ROLLBACK;
