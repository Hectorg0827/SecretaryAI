-- V15: immutability and claim binding for synthetic email only.
\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE
 a UUID; b UUID; actor UUID; da UUID; queued JSONB; claimed JSONB;
 e UUID; denied BOOLEAN; original_body TEXT; n INTEGER;
BEGIN
 INSERT INTO companies(name) VALUES('Email snapshot A') RETURNING id INTO a;
 INSERT INTO companies(name) VALUES('Email snapshot B') RETURNING id INTO b;
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(a,'email-snap-'||a||'@example.test','test','owner')
 RETURNING id INTO actor;
 INSERT INTO drafts(company_id,created_by,action_type,content,status)
 VALUES(a,'ai','email_reply',
  '{"to":"recipient@example.test","subject":"Original","body":"Approved body"}'::jsonb,
  'pending') RETURNING id INTO da;
 queued:=secretary_internal.approve_and_queue_email(
  a,da,actor,
  '{"to":"recipient@example.test","subject":"Original","body":"Approved body"}'::jsonb,
  'immutable-snap-1');
 e:=(queued->>'execution_id')::UUID;
 SELECT body INTO original_body FROM agent_execution_email_payloads
  WHERE company_id=a AND execution_id=e;
 IF original_body<>'Approved body' THEN
   RAISE EXCEPTION 'approval did not snapshot original body';
 END IF;
 UPDATE drafts SET content='{"to":"changed@example.test","body":"Changed later"}'::jsonb
  WHERE company_id=a AND id=da;
 claimed:=secretary_internal.claim_approved_email(a,e,'worker-one');
 IF claimed->>'claimed'<>'true' OR
    claimed->>'recipient'<>'recipient@example.test' OR
    claimed->>'body'<>'Approved body' OR
    length(claimed->>'payload_sha256')<>64 THEN
   RAISE EXCEPTION 'worker observed mutable draft data';
 END IF;
 claimed:=secretary_internal.claim_approved_email(a,e,'worker-two');
 IF claimed->>'claimed'<>'false' OR claimed ? 'body' THEN
   RAISE EXCEPTION 'losing worker received a payload';
 END IF;
 denied:=FALSE;
 BEGIN
  PERFORM secretary_internal.claim_approved_email(b,e,'foreign-worker');
 EXCEPTION WHEN no_data_found THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'cross-tenant payload was claimed'; END IF;
 denied:=FALSE;
 BEGIN
   UPDATE agent_execution_email_payloads SET body='Tampered'
    WHERE company_id=a AND execution_id=e;
 EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'approved snapshot was changed'; END IF;
 denied:=FALSE;
 BEGIN
   DELETE FROM agent_execution_email_payloads
    WHERE company_id=a AND execution_id=e;
 EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'approved snapshot was deleted'; END IF;
 IF has_table_privilege('authenticated','agent_execution_email_payloads','SELECT')
  OR has_table_privilege('service_role','agent_execution_email_payloads','SELECT')
  OR has_table_privilege('secretary_executor','agent_execution_email_payloads','SELECT') THEN
   RAISE EXCEPTION 'raw approved email exposed to runtime roles';
 END IF;
 SELECT count(*) INTO n FROM agent_execution_email_payloads
  WHERE company_id=a AND execution_id=e;
 IF n<>1 THEN RAISE EXCEPTION 'snapshot lost'; END IF;
END
$test$;
ROLLBACK;
