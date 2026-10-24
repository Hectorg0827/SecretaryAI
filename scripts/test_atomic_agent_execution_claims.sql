-- V11: enqueue/claim/finalize protocol; zero external delivery.
\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE
 a UUID; b UUID; da UUID; db UUID; qa UUID; qb UUID;
 first JSONB; replay JSONB; claim JSONB; second JSONB; finish JSONB;
 denied BOOLEAN; count_a INTEGER;
BEGIN
 INSERT INTO companies(name) VALUES('Claim A') RETURNING id INTO a;
 INSERT INTO companies(name) VALUES('Claim B') RETURNING id INTO b;
 INSERT INTO drafts(company_id,created_by,action_type,content,status)
 VALUES(a,'ai','draft_customer_email','{}'::jsonb,'approved')
 RETURNING id INTO da;
 INSERT INTO drafts(company_id,created_by,action_type,content,status)
 VALUES(b,'ai','draft_customer_email','{}'::jsonb,'pending')
 RETURNING id INTO db;
 denied:=FALSE;
 BEGIN
   PERFORM secretary_internal.enqueue_agent_execution(b,db,'not-approved');
 EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'unapproved draft queued'; END IF;
 denied:=FALSE;
 BEGIN
   PERFORM secretary_internal.enqueue_agent_execution(b,da,'cross-tenant');
 EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'cross-tenant draft queued'; END IF;

 first:=secretary_internal.enqueue_agent_execution(a,da,'email-order-123');
 replay:=secretary_internal.enqueue_agent_execution(a,da,'email-order-123');
 qa:=(first->>'execution_id')::UUID;
 IF first->>'execution_id'<>replay->>'execution_id'
   OR first->>'state'<>'queued' THEN
   RAISE EXCEPTION 'idempotent enqueue failed';
 END IF;
 denied:=FALSE;
 BEGIN
   PERFORM secretary_internal.enqueue_agent_execution(a,da,'different-key');
 EXCEPTION WHEN unique_violation THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'conflicting replay accepted'; END IF;

 claim:=secretary_internal.claim_agent_execution(a,qa,'worker-one');
 IF claim->>'claimed'<>'true' OR (claim->>'attempt')::INTEGER<>1 THEN
   RAISE EXCEPTION 'first claim failed';
 END IF;
 second:=secretary_internal.claim_agent_execution(a,qa,'worker-two');
 IF second->>'claimed'<>'false' THEN
   RAISE EXCEPTION 'duplicate worker claim succeeded';
 END IF;
 finish:=secretary_internal.finish_agent_execution(
   a,qa,gen_random_uuid(),'completed','wrong-token');
 IF finish->>'finalized'<>'false' THEN
   RAISE EXCEPTION 'stale worker finalized execution';
 END IF;
 finish:=secretary_internal.finish_agent_execution(
   b,qa,(claim->>'claim_token')::UUID,'completed','other-company');
 IF finish->>'finalized'<>'false' THEN
   RAISE EXCEPTION 'other tenant finalized execution';
 END IF;
 finish:=secretary_internal.finish_agent_execution(
   a,qa,(claim->>'claim_token')::UUID,'needs_reconciliation',NULL,'unknown-send');
 IF finish->>'finalized'<>'true' THEN
   RAISE EXCEPTION 'uncertain execution was not recorded';
 END IF;
 second:=secretary_internal.claim_agent_execution(a,qa,'worker-three');
 IF second->>'claimed'<>'false' THEN
   RAISE EXCEPTION 'uncertain external effect re-executed';
 END IF;

 IF has_function_privilege('authenticated',
    'secretary_internal.enqueue_agent_execution(uuid,uuid,text)','EXECUTE') OR
    has_function_privilege('anon',
    'secretary_internal.claim_agent_execution(uuid,uuid,text)','EXECUTE') THEN
   RAISE EXCEPTION 'user role can invoke privileged claim function';
 END IF;
 SELECT count(*) INTO count_a FROM agent_executions WHERE company_id=a;
 IF count_a<>1 THEN RAISE EXCEPTION 'multiple sends became eligible'; END IF;
END
$test$;
ROLLBACK;
