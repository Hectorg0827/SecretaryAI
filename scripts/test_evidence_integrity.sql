-- V8 integrity attestation: authorization, digest checks, RLS and immutability.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE integrity_test_ids(tenant_a UUID,tenant_b UUID);
DO $proof$
DECLARE
  a UUID;
  b UUID;
  wh UUID;
  creator UUID;
  approver UUID;
  outsider UUID;
  draft JSONB;
  claim JSONB;
  attestation JSONB;
  replay JSONB;
  eid UUID;
  bad BOOLEAN;
  n INTEGER;
  key TEXT;
BEGIN
 INSERT INTO companies(name) VALUES ('Hash tenant A') RETURNING id INTO a;
 INSERT INTO companies(name) VALUES ('Hash tenant B') RETURNING id INTO b;
 INSERT INTO integrity_test_ids VALUES(a,b);
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(a,'hash-creator-'||a||'@example.test','test','back_office')
 RETURNING id INTO creator;
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(a,'hash-approver-'||a||'@example.test','test','manager')
 RETURNING id INTO approver;
 INSERT INTO users(company_id,email,hashed_password,role)
 VALUES(b,'hash-outsider-'||b||'@example.test','test','owner')
 RETURNING id INTO outsider;
 INSERT INTO stock_skus(company_id,sku,product_name,base_unit)
 VALUES(a,'HASH-SKU','Stock','case');
 INSERT INTO stock_warehouses(company_id,code,name)
 VALUES(a,'WH','Warehouse') RETURNING id INTO wh;
 draft:=create_warehouse_purchase_order(a,'PO-HASH','SUP',wh,
   '[{"sku":"HASH-SKU","qty_ordered":20}]'::JSONB,creator);
 PERFORM approve_warehouse_purchase_order(a,'PO-HASH',approver);
 key:=a::TEXT||'/purchasing/received.pdf';
 claim:=record_purchasing_evidence_claim(a,'PO-HASH','HASH-1','bill_of_lading',
   key,repeat('a',64),NULL,NULL,creator);
 eid:=(claim->>'evidence_id')::UUID;

 bad:=FALSE;
 BEGIN
  PERFORM attest_purchasing_evidence_integrity(a,eid,repeat('b',64),
    200,'application/pdf',creator);
 EXCEPTION WHEN check_violation THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'claimed hash mismatch allowed'; END IF;
 bad:=FALSE;
 BEGIN
  PERFORM attest_purchasing_evidence_integrity(a,eid,repeat('a',64),
    200,'application/pdf',outsider);
 EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'foreign actor signed integrity check'; END IF;
 bad:=FALSE;
 BEGIN
  PERFORM attest_purchasing_evidence_integrity(b,eid,repeat('a',64),
    200,'application/pdf',outsider);
 EXCEPTION WHEN no_data_found THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'foreign tenant signed another claim'; END IF;
 bad:=FALSE;
 BEGIN
  PERFORM attest_purchasing_evidence_integrity(a,eid,repeat('a',64),
    200,'image/png',creator);
 EXCEPTION WHEN check_violation THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'wrong MIME extension accepted'; END IF;
 bad:=FALSE;
 BEGIN
  PERFORM attest_purchasing_evidence_integrity(a,eid,repeat('a',64),
    8388609,'application/pdf',creator);
 EXCEPTION WHEN invalid_parameter_value THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'oversize attestation accepted'; END IF;

 attestation:=attest_purchasing_evidence_integrity(
   a,eid,repeat('a',64),200,'application/pdf',creator);
 IF attestation->>'integrity_status'<>'sha256_matched_at_upload' OR
   attestation->>'malware_scan_status'<>'not_scanned' OR
   attestation->>'safe_to_view'<>'false' OR
   attestation->>'accounting_reconciled'<>'false' THEN
   RAISE EXCEPTION 'incorrect integrity claims';
 END IF;
 replay:=attest_purchasing_evidence_integrity(
   a,eid,repeat('a',64),200,'application/pdf',creator);
 IF replay->>'replayed'<>'true' OR
   replay->>'integrity_check_id'<>attestation->>'integrity_check_id' THEN
   RAISE EXCEPTION 'integrity replay created duplicate';
 END IF;
 bad:=FALSE;
 BEGIN
  PERFORM attest_purchasing_evidence_integrity(
    a,eid,repeat('a',64),201,'application/pdf',creator);
 EXCEPTION WHEN unique_violation THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'different size reused integrity key'; END IF;
 bad:=FALSE;
 BEGIN
  UPDATE purchasing_evidence_integrity_checks SET malware_scan_status='not_scanned'
  WHERE company_id=a AND evidence_id=eid;
 EXCEPTION WHEN check_violation THEN bad:=TRUE;
 END;
 IF NOT bad THEN RAISE EXCEPTION 'integrity record was mutable'; END IF;
 SELECT count(*) INTO n FROM stock_movements WHERE company_id=a;
 IF n<>0 THEN RAISE EXCEPTION 'integrity posted stock'; END IF;
END
$proof$;

DO $grants$
BEGIN
 IF has_function_privilege('authenticated',
   'attest_purchasing_evidence_integrity(uuid,uuid,text,integer,text,uuid)',
   'EXECUTE') OR
   has_table_privilege('service_role','purchasing_evidence_integrity_checks','INSERT')
 THEN RAISE EXCEPTION 'client integrity-write bypass'; END IF;
END
$grants$;
SELECT set_config('request.jwt.claims',
 jsonb_build_object('company_id',tenant_a)::TEXT,FALSE) FROM integrity_test_ids;
SET ROLE authenticated;
DO $rls$
DECLARE n INTEGER;
BEGIN
 SELECT count(*) INTO n FROM purchasing_evidence_integrity_checks;
 IF n<>1 THEN RAISE EXCEPTION 'tenant A integrity visibility wrong: %',n; END IF;
END
$rls$;
RESET ROLE;
SELECT set_config('request.jwt.claims',
 jsonb_build_object('company_id',tenant_b)::TEXT,FALSE) FROM integrity_test_ids;
SET ROLE authenticated;
DO $rls$
DECLARE n INTEGER;
BEGIN
 SELECT count(*) INTO n FROM purchasing_evidence_integrity_checks;
 IF n<>0 THEN RAISE EXCEPTION 'foreign tenant read integrity record'; END IF;
END
$rls$;
RESET ROLE;
ROLLBACK;
\echo 'PASS: V8 integrity attestation only, tenant RLS, immutable and no malware clearance'
