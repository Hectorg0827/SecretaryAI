-- V7 PostgreSQL integration: document provenance is a claim, not verified bytes.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE evidence_test_ids(tenant_a UUID,tenant_b UUID);
DO $evidence$
DECLARE
  a UUID;
  b UUID;
  wa UUID;
  creator UUID;
  approver UUID;
  foreign_user UUID;
  draft JSONB;
  first_claim JSONB;
  replay_claim JSONB;
  v_po_id UUID;
  n INTEGER;
  bad BOOLEAN;
  qty NUMERIC;
BEGIN
  INSERT INTO companies(name) VALUES('Evidence tenant A') RETURNING id INTO a;
  INSERT INTO companies(name) VALUES('Evidence tenant B') RETURNING id INTO b;
  INSERT INTO evidence_test_ids VALUES (a,b);
  INSERT INTO users(company_id,email,hashed_password,role)
    VALUES(a,'evidence-creator-'||a||'@example.test','test','back_office')
    RETURNING id INTO creator;
  INSERT INTO users(company_id,email,hashed_password,role)
    VALUES(a,'evidence-approver-'||a||'@example.test','test','manager')
    RETURNING id INTO approver;
  INSERT INTO users(company_id,email,hashed_password,role)
    VALUES(b,'evidence-foreign-'||b||'@example.test','test','owner')
    RETURNING id INTO foreign_user;
  INSERT INTO stock_skus(company_id,sku,product_name,base_unit)
    VALUES(a,'POTEST','Cases','case');
  INSERT INTO stock_warehouses(company_id,code,name)
    VALUES(a,'RECEIVING','Warehouse') RETURNING id INTO wa;
  draft:=create_warehouse_purchase_order(a,'PO-EVIDENCE-1','SUP',wa,
    '[{"sku":"POTEST","qty_ordered":1200}]'::jsonb,creator);
  v_po_id:=(draft->>'po_id')::UUID;

  bad:=FALSE;
  BEGIN
    PERFORM record_purchasing_evidence_claim(a,'PO-EVIDENCE-1','BOL-1',
      'bill_of_lading',a::TEXT||'/purchasing/bol.pdf',
      repeat('a',64),NULL,NULL,creator);
  EXCEPTION WHEN check_violation THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'draft PO accepted evidence'; END IF;
  PERFORM approve_warehouse_purchase_order(a,'PO-EVIDENCE-1',approver);

  first_claim:=record_purchasing_evidence_claim(
    a,'PO-EVIDENCE-1','FREIGHT-1','freight_invoice',
    a::TEXT||'/purchasing/ship-1.pdf',
    repeat('a',64),10000.20,'USD',creator);
  IF first_claim->>'verification_status'<>'unverified' OR
     first_claim->>'accounting_reconciled'<>'false' OR
     first_claim->>'replayed'<>'false' THEN
       RAISE EXCEPTION 'submitted evidence falsely verified/accounted';
  END IF;
  replay_claim:=record_purchasing_evidence_claim(
    a,'PO-EVIDENCE-1','FREIGHT-1','freight_invoice',
    a::TEXT||'/purchasing/ship-1.pdf',
    repeat('a',64),10000.20,'USD',creator);
  IF replay_claim->>'replayed'<>'true' OR
     replay_claim->>'evidence_id'<>first_claim->>'evidence_id' THEN
       RAISE EXCEPTION 'evidence submission replay is not idempotent';
  END IF;
  SELECT count(*) INTO n FROM purchasing_evidence_claims WHERE company_id=a;
  IF n<>1 THEN RAISE EXCEPTION 'duplicate evidence created'; END IF;

  bad:=FALSE;
  BEGIN
    PERFORM record_purchasing_evidence_claim(
      a,'PO-EVIDENCE-1','FREIGHT-1','freight_invoice',
      a::TEXT||'/purchasing/ship-1.pdf',
      repeat('b',64),10000.20,'USD',creator);
  EXCEPTION WHEN unique_violation THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'same key accepted modified hash'; END IF;
  bad:=FALSE;
  BEGIN
    PERFORM record_purchasing_evidence_claim(
      a,'PO-EVIDENCE-1','FREIGHT-2','freight_invoice',
      a::TEXT||'/purchasing/../private.pdf',
      repeat('b',64),10,'USD',creator);
  EXCEPTION WHEN invalid_parameter_value THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'path traversal in evidence key accepted'; END IF;

  bad:=FALSE;
  BEGIN
    PERFORM record_purchasing_evidence_claim(
      a,'PO-EVIDENCE-1','FREIGHT-2','freight_invoice',
      b::TEXT||'/purchasing/cross-tenant.pdf',
      repeat('b',64),10,'USD',creator);
  EXCEPTION WHEN invalid_parameter_value THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'foreign tenant object accepted'; END IF;

  bad:=FALSE;
  BEGIN
    PERFORM record_purchasing_evidence_claim(
      a,'PO-EVIDENCE-1','FREIGHT-2','freight_invoice',
      a::TEXT||'/purchasing/f2.pdf',repeat('b',64),10,'USD',foreign_user);
  EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'foreign tenant actor allowed'; END IF;

  bad:=FALSE;
  BEGIN
    PERFORM record_purchasing_evidence_claim(
      a,'PO-EVIDENCE-1','FREIGHT-2','freight_invoice',
      a::TEXT||'/purchasing/f2.pdf',repeat('b',64),10.003,'USD',creator);
  EXCEPTION WHEN invalid_parameter_value THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'silent amount rounding allowed'; END IF;

  -- Multiple currencies are preserved independently, not summed.
  PERFORM record_purchasing_evidence_claim(
    a,'PO-EVIDENCE-1','BROKER-1','broker_invoice',
    a::TEXT||'/purchasing/broker.pdf',repeat('b',64),100,'GBP',creator);
  SELECT count(*) INTO n FROM purchasing_evidence_claims
    WHERE company_id=a AND verification_status='unverified';
  IF n<>2 THEN RAISE EXCEPTION 'missing currency-specific evidence'; END IF;

  bad:=FALSE;
  BEGIN
    UPDATE purchasing_evidence_claims SET claimed_amount=0
      WHERE id=(first_claim->>'evidence_id')::UUID;
  EXCEPTION WHEN check_violation THEN bad:=TRUE;
  END;
  IF NOT bad THEN RAISE EXCEPTION 'evidence rewrite not prohibited'; END IF;

  SELECT count(*) INTO n FROM stock_movements WHERE company_id=a;
  IF n<>0 THEN RAISE EXCEPTION 'evidence metadata illegally posted stock'; END IF;
  SELECT qty_received INTO qty FROM purchasing_order_lines
    WHERE company_id=a AND po_id=v_po_id;
  IF qty<>0 THEN RAISE EXCEPTION 'evidence metadata illegally received cases'; END IF;
END
$evidence$;

DO $permissions$
BEGIN
  IF has_table_privilege('service_role','purchasing_evidence_claims','INSERT') OR
     has_function_privilege('authenticated',
       'record_purchasing_evidence_claim(uuid,text,text,text,text,text,numeric,text,uuid)',
       'EXECUTE') THEN
    RAISE EXCEPTION 'evidence mutation bypass available';
  END IF;
END
$permissions$;

SELECT set_config('request.jwt.claims',
  jsonb_build_object('company_id',tenant_a)::TEXT,FALSE)
  FROM evidence_test_ids;
SET ROLE authenticated;
DO $tenant$
DECLARE n INTEGER;
BEGIN
 SELECT count(*) INTO n FROM purchasing_evidence_claims;
 IF n<>2 THEN RAISE EXCEPTION 'tenant A evidence lost or leaked: %',n; END IF;
END
$tenant$;
RESET ROLE;
SELECT set_config('request.jwt.claims',
  jsonb_build_object('company_id',tenant_b)::TEXT,FALSE)
  FROM evidence_test_ids;
SET ROLE authenticated;
DO $tenant$
DECLARE n INTEGER;
BEGIN
 SELECT count(*) INTO n FROM purchasing_evidence_claims;
 IF n<>0 THEN RAISE EXCEPTION 'cross-tenant evidence leaked'; END IF;
END
$tenant$;
RESET ROLE;
ROLLBACK;
\echo 'PASS: V7 evidence metadata is immutable, scoped, idempotent and unverified'
