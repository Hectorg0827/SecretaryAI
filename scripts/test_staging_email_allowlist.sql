-- V17: wrong recipient must not consume a queue claim.
\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE
 a UUID; actor UUID; draft UUID; execution_id UUID;
 queued JSONB; claimed JSONB; denied BOOLEAN; state_after TEXT;
BEGIN
 INSERT INTO companies(name) VALUES('Staging email A') RETURNING id INTO a;
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(a,'staging-'||a||'@example.test','test','owner')
 RETURNING id INTO actor;
 INSERT INTO drafts(company_id,created_by,action_type,content,status)
 VALUES(a,'ai','email_reply',
 '{"to":"pilot@example.test","body":"Synthetic acceptance"}'::jsonb,
 'pending') RETURNING id INTO draft;
 queued:=secretary_internal.approve_and_queue_email(
   a,draft,actor,
   '{"to":"pilot@example.test","body":"Synthetic acceptance"}'::jsonb,
   'v17-staging');
 execution_id:=(queued->>'execution_id')::UUID;
 denied:=FALSE;
 BEGIN
   PERFORM secretary_internal.claim_staging_email(
     a,execution_id,'worker-wrong','customer@example.test');
 EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'wrong recipient claim succeeded'; END IF;
 SELECT state INTO state_after FROM agent_executions
 WHERE company_id=a AND id=execution_id;
 IF state_after<>'queued' THEN
   RAISE EXCEPTION 'denied recipient consumed execution';
 END IF;
 denied:=FALSE;
 BEGIN
   PERFORM secretary_internal.claim_staging_email(
     a,execution_id,'worker-list','pilot@example.test,other@example.test');
 EXCEPTION WHEN invalid_parameter_value THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'recipient list accepted'; END IF;
 claimed:=secretary_internal.claim_staging_email(
   a,execution_id,'worker-correct','pilot@example.test');
 IF claimed->>'claimed'<>'true' OR claimed->>'recipient'<>'pilot@example.test' THEN
   RAISE EXCEPTION 'valid allowlisted claim failed';
 END IF;
 claimed:=secretary_internal.claim_staging_email(
   a,execution_id,'worker-second','pilot@example.test');
 IF claimed->>'claimed'<>'false' OR claimed ? 'body' THEN
   RAISE EXCEPTION 'duplicate worker received email';
 END IF;
 IF has_function_privilege('authenticated',
   'secretary_internal.claim_staging_email(uuid,uuid,text,text)','EXECUTE')
   OR has_function_privilege('service_role',
   'secretary_internal.claim_staging_email(uuid,uuid,text,text)','EXECUTE')
 THEN
   RAISE EXCEPTION 'untrusted database role can claim staging email';
 END IF;
END
$test$;
ROLLBACK;
