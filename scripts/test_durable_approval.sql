-- V14: approval and enqueue are one PostgreSQL transaction.
\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE
 a UUID; b UUID; approver UUID; outsider UUID;
 draft_a UUID; draft_b UUID; first JSONB; replay JSONB;
 edited JSONB; denied BOOLEAN; n INTEGER; status_after TEXT;
BEGIN
 INSERT INTO companies(name) VALUES('Approval test A') RETURNING id INTO a;
 INSERT INTO companies(name) VALUES('Approval test B') RETURNING id INTO b;
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(a,'approver-'||a||'@example.test','test','owner')
 RETURNING id INTO approver;
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(b,'outsider-'||b||'@example.test','test','owner')
 RETURNING id INTO outsider;
 INSERT INTO drafts(company_id,created_by,action_type,content,status)
 VALUES(a,'ai','email_reply','{"to":"a@example.test","body":"Synthetic"}'::jsonb,'pending')
 RETURNING id INTO draft_a;
 INSERT INTO drafts(company_id,created_by,action_type,content,status)
 VALUES(a,'ai','email_reply','{"to":"b@example.test","body":"Synthetic"}'::jsonb,'pending')
 RETURNING id INTO draft_b;

 denied:=FALSE;
 BEGIN
  PERFORM secretary_internal.approve_and_queue_email(
    a,draft_a,outsider,'{"to":"a@example.test","body":"Synthetic"}'::jsonb,'same-idem');
 EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'cross-tenant approver succeeded'; END IF;

 denied:=FALSE;
 BEGIN
  PERFORM secretary_internal.approve_and_queue_email(
    a,draft_a,approver,'{"to":"stale@example.test","body":"Synthetic"}'::jsonb,'same-idem');
 EXCEPTION WHEN serialization_failure THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'stale content approved'; END IF;

 first:=secretary_internal.approve_and_queue_email(
   a,draft_a,approver,'{"to":"a@example.test","body":"Synthetic"}'::jsonb,'same-idem');
 replay:=secretary_internal.approve_and_queue_email(
   a,draft_a,approver,'{"to":"a@example.test","body":"Synthetic"}'::jsonb,'same-idem');
 IF first->>'queued'<>'true' OR replay->>'already_processed'<>'true'
    OR first->>'execution_id'<>replay->>'execution_id' THEN
   RAISE EXCEPTION 'approval replay was not idempotent';
 END IF;
 SELECT count(*) INTO n FROM agent_executions
 WHERE company_id=a AND draft_id=draft_a;
 IF n<>1 THEN RAISE EXCEPTION 'duplicate execution was created'; END IF;
 SELECT status INTO status_after FROM drafts
 WHERE company_id=a AND id=draft_a;
 IF status_after<>'approved' THEN
  RAISE EXCEPTION 'draft not approved atomically';
 END IF;

 denied:=FALSE;
 BEGIN
  PERFORM secretary_internal.approve_and_queue_email(
    a,draft_b,approver,'{"to":"b@example.test","body":"Synthetic"}'::jsonb,'same-idem');
 EXCEPTION WHEN unique_violation THEN denied:=TRUE;
 END;
 IF NOT denied THEN RAISE EXCEPTION 'conflicting idempotency key accepted'; END IF;
 SELECT status INTO status_after FROM drafts WHERE id=draft_b;
 IF status_after<>'pending' THEN
   RAISE EXCEPTION 'failed enqueue left an approved draft';
 END IF;

 edited:=secretary_internal.approve_and_queue_email(
   a,draft_b,approver,'{"to":"b@example.test","body":"Synthetic"}'::jsonb,
   'new-idem','{"to":"edited@example.test","body":"Synthetic"}'::jsonb);
 IF edited->>'status'<>'edited_and_approved' THEN
   RAISE EXCEPTION 'edited approval not persisted';
 END IF;
 IF has_function_privilege('authenticated',
   'secretary_internal.approve_and_queue_email(uuid,uuid,uuid,jsonb,text,jsonb)',
   'EXECUTE') OR has_function_privilege('service_role',
   'secretary_internal.approve_and_queue_email(uuid,uuid,uuid,jsonb,text,jsonb)',
   'EXECUTE') THEN
   RAISE EXCEPTION 'broad role can invoke atomic approval';
 END IF;
END
$test$;
ROLLBACK;
