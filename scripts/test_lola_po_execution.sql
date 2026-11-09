-- Lola purchase-order durable approval: real PostgreSQL, rollback-only fixtures.
\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE
  a UUID; b UUID; approver UUID; rep UUID; outsider UUID; d1 UUID; d2 UUID; d3 UUID;
  good JSONB := '{"vendor_id":"V-7","lines":[{"item_id":"SKU-1","quantity":500,"unit_cost":"3.25","description":"Olive Oil"}],"total":"1625.00"}';
  first JSONB; replay JSONB; claim JSONB; denied BOOLEAN; n INTEGER;
BEGIN
  INSERT INTO companies(name) VALUES('PO A') RETURNING id INTO a;
  INSERT INTO companies(name) VALUES('PO B') RETURNING id INTO b;
  INSERT INTO users(company_id,email,hashed_password,role) VALUES(a,'po-owner-'||a||'@t.test','x','owner') RETURNING id INTO approver;
  INSERT INTO users(company_id,email,hashed_password,role) VALUES(a,'po-rep-'||a||'@t.test','x','sales_rep') RETURNING id INTO rep;
  INSERT INTO users(company_id,email,hashed_password,role) VALUES(b,'po-out-'||b||'@t.test','x','owner') RETURNING id INTO outsider;
  INSERT INTO drafts(company_id,created_by,action_type,content,status) VALUES(a,'ai','purchase_order',good,'pending') RETURNING id INTO d1;
  INSERT INTO drafts(company_id,created_by,action_type,content,status)
    VALUES(a,'ai','purchase_order','{"vendor_id":null,"lines":[{"item_id":"SKU-1","quantity":5,"unit_cost":"1"}],"total":"5.00"}','pending') RETURNING id INTO d2;
  INSERT INTO drafts(company_id,created_by,action_type,content,status)
    VALUES(a,'ai','purchase_order','{"vendor_id":"V-7","lines":[{"item_id":"SKU-1","quantity":5,"unit_cost":"1"}],"total":"5000.00"}','pending') RETURNING id INTO d3;

  denied:=FALSE;
  BEGIN PERFORM secretary_internal.approve_and_queue_purchase_order(a,d1,outsider,good,'k1');
  EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'cross-tenant PO approver succeeded'; END IF;

  denied:=FALSE;
  BEGIN PERFORM secretary_internal.approve_and_queue_purchase_order(a,d1,rep,good,'k1');
  EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'sales rep approved a PO'; END IF;

  denied:=FALSE;
  BEGIN PERFORM secretary_internal.approve_and_queue_purchase_order(a,d2,approver,
    (SELECT content FROM drafts WHERE id=d2),'k2');
  EXCEPTION WHEN invalid_parameter_value THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'PO without vendor queued'; END IF;

  denied:=FALSE;
  BEGIN PERFORM secretary_internal.approve_and_queue_purchase_order(a,d3,approver,
    (SELECT content FROM drafts WHERE id=d3),'k3');
  EXCEPTION WHEN invalid_parameter_value THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'PO total mismatch queued'; END IF;

  denied:=FALSE;
  BEGIN PERFORM secretary_internal.approve_and_queue_purchase_order(a,d1,approver,
    '{"vendor_id":"V-9"}'::jsonb,'k1');
  EXCEPTION WHEN serialization_failure THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'stale PO content approved'; END IF;

  -- An approver edit that inflates quantities must still match the approved total.
  denied:=FALSE;
  BEGIN PERFORM secretary_internal.approve_and_queue_purchase_order(a,d1,approver,good,'k1',
    '{"vendor_id":"V-7","lines":[{"item_id":"SKU-1","quantity":5000,"unit_cost":"3.25"}],"total":"1625.00"}');
  EXCEPTION WHEN invalid_parameter_value THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'inflated PO edit queued'; END IF;

  first:=secretary_internal.approve_and_queue_purchase_order(a,d1,approver,good,'k1');
  replay:=secretary_internal.approve_and_queue_purchase_order(a,d1,approver,good,'k1');
  IF first->>'queued'<>'true' OR replay->>'already_processed'<>'true'
     OR first->>'execution_id'<>replay->>'execution_id' THEN
    RAISE EXCEPTION 'PO approval replay was not idempotent';
  END IF;
  SELECT count(*) INTO n FROM agent_executions WHERE company_id=a AND draft_id=d1;
  IF n<>1 THEN RAISE EXCEPTION 'duplicate PO execution'; END IF;

  denied:=FALSE;
  BEGIN UPDATE agent_execution_po_payloads SET vendor_id='V-X' WHERE company_id=a;
  EXCEPTION WHEN insufficient_privilege THEN denied:=TRUE; END;
  IF NOT denied THEN RAISE EXCEPTION 'approved PO payload rewritten'; END IF;

  claim:=secretary_internal.claim_approved_purchase_order(a,(first->>'execution_id')::UUID,'worker-1');
  IF claim->>'claimed'<>'true' OR claim->>'vendor_id'<>'V-7' OR (claim->>'total')::NUMERIC<>1625.00
     OR jsonb_array_length(claim->'lines')<>1 THEN
    RAISE EXCEPTION 'PO claim did not return the immutable payload: %', claim;
  END IF;
  claim:=secretary_internal.claim_approved_purchase_order(a,(first->>'execution_id')::UUID,'worker-2');
  IF claim->>'claimed'<>'false' THEN RAISE EXCEPTION 'PO claimed twice'; END IF;

  IF has_function_privilege('service_role',
       'secretary_internal.approve_and_queue_purchase_order(uuid,uuid,uuid,jsonb,text,jsonb)','EXECUTE')
     OR has_function_privilege('authenticated',
       'secretary_internal.claim_approved_purchase_order(uuid,uuid,text)','EXECUTE')
     OR NOT has_function_privilege('secretary_executor',
       'secretary_internal.claim_approved_purchase_order(uuid,uuid,text)','EXECUTE')
     OR has_table_privilege('secretary_executor','agent_execution_po_payloads','SELECT') THEN
    RAISE EXCEPTION 'PO execution privileges are wrong';
  END IF;
END
$test$;
ROLLBACK;
