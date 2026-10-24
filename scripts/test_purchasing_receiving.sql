-- End-to-end atomic PO approval and receiving regression on real Postgres.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE po_acceptance_ids(company_a UUID,company_b UUID,po_id UUID);
DO $test$
DECLARE
    a UUID;
    b UUID;
    wa UUID;
    wb UUID;
    creator UUID;
    reviewer UUID;
    receiver UUID;
    outsider UUID;
    po JSONB;
    result JSONB;
    replay JSONB;
    receipt_lines JSONB;
    bad BOOLEAN;
    q NUMERIC;
    n INTEGER;
    v_po UUID;
BEGIN
    INSERT INTO companies(name) VALUES('PO Integration Test A') RETURNING id INTO a;
    INSERT INTO companies(name) VALUES('PO Integration Test B') RETURNING id INTO b;
    INSERT INTO users(company_id,email,hashed_password,role)
      VALUES(a,'po-creator-'||a||'@example.test','test','back_office')
      RETURNING id INTO creator;
    INSERT INTO users(company_id,email,hashed_password,role)
      VALUES(a,'po-reviewer-'||a||'@example.test','test','manager')
      RETURNING id INTO reviewer;
    INSERT INTO users(company_id,email,hashed_password,role)
      VALUES(a,'po-receiver-'||a||'@example.test','test','back_office')
      RETURNING id INTO receiver;
    INSERT INTO users(company_id,email,hashed_password,role)
      VALUES(b,'po-outsider-'||b||'@example.test','test','owner')
      RETURNING id INTO outsider;
    INSERT INTO stock_skus(company_id,sku,product_name,base_unit)
      VALUES(a,'TEST-WINE','Wine','case'),
            (b,'TEST-WINE','Wine','case');
    INSERT INTO stock_warehouses(company_id,code,name)
      VALUES(a,'BRONX','Bronx Receiving') RETURNING id INTO wa;
    INSERT INTO stock_warehouses(company_id,code,name)
      VALUES(b,'BRONX','Other Tenant') RETURNING id INTO wb;

    po := create_warehouse_purchase_order(
      a,'PO-E2E-1200','SUP-1',wa,
      '[{"sku":"TEST-WINE","qty_ordered":1200}]'::jsonb,creator);
    IF po->>'status' <> 'draft' OR po->>'inventory_posted' <> 'false' THEN
      RAISE EXCEPTION 'draft PO not created safely';
    END IF;
    v_po := (po->>'po_id')::UUID;
    INSERT INTO po_acceptance_ids VALUES(a,b,v_po);

    bad:=FALSE;
    BEGIN
      PERFORM receive_warehouse_purchase_order(
        a,'PO-E2E-1200','REC-1',
        '[{"sku":"TEST-WINE","accepted_qty":1180,"damaged_qty":20}]'::jsonb,
        receiver,reviewer);
    EXCEPTION WHEN check_violation THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'unapproved receipt posted'; END IF;

    bad:=FALSE;
    BEGIN
      PERFORM approve_warehouse_purchase_order(a,'PO-E2E-1200',creator);
    EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'PO creator could approve'; END IF;
    bad:=FALSE;
    BEGIN
      PERFORM approve_warehouse_purchase_order(a,'PO-E2E-1200',outsider);
    EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'foreign tenant approved PO'; END IF;
    PERFORM approve_warehouse_purchase_order(a,'PO-E2E-1200',reviewer);

    receipt_lines := '[{"sku":"TEST-WINE","lot_code":"LOT-X","accepted_qty":1180,"damaged_qty":20}]'::jsonb;
    bad:=FALSE;
    BEGIN
      PERFORM receive_warehouse_purchase_order(
        a,'PO-E2E-1200','REC-1',receipt_lines,receiver,receiver);
    EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'self-approval of receipt allowed'; END IF;
    bad:=FALSE;
    BEGIN
      PERFORM receive_warehouse_purchase_order(
        a,'PO-E2E-1200','REC-1',receipt_lines,receiver,outsider);
    EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'foreign approval accepted'; END IF;

    -- Independent receiving approval is a separate authenticated action.
    result:=stage_warehouse_receipt(
      a,'PO-E2E-1200','REC-1',receipt_lines,receiver);
    IF result->>'status'<>'pending' OR result->>'inventory_posted'<>'false'
    THEN RAISE EXCEPTION 'receipt was prematurely posted'; END IF;
    DECLARE
      v_stage UUID := (result->>'staged_receipt_id')::UUID;
    BEGIN
      replay:=stage_warehouse_receipt(
        a,'PO-E2E-1200','REC-1',receipt_lines,receiver);
      IF replay->>'replayed'<>'true' OR
         replay->>'staged_receipt_id'<>result->>'staged_receipt_id'
      THEN RAISE EXCEPTION 'receipt staging replay failed'; END IF;
      bad:=FALSE;
      BEGIN
        PERFORM stage_warehouse_receipt(
          a,'PO-E2E-1200','REC-1',
          '[{"sku":"TEST-WINE","accepted_qty":999}]'::jsonb,receiver);
      EXCEPTION WHEN unique_violation THEN bad:=TRUE;
      END;
      IF NOT bad THEN RAISE EXCEPTION 'staging key reused'; END IF;
      bad:=FALSE;
      BEGIN
        PERFORM approve_staged_warehouse_receipt(a,v_stage,receiver);
      EXCEPTION WHEN insufficient_privilege THEN bad:=TRUE;
      END;
      IF NOT bad THEN RAISE EXCEPTION 'receiver approved own receipt'; END IF;
      result:=approve_staged_warehouse_receipt(a,v_stage,reviewer);
      replay:=approve_staged_warehouse_receipt(a,v_stage,reviewer);
      IF replay->>'replayed'<>'true' OR
         replay->>'receipt_id'<>result->>'receipt_id'
      THEN RAISE EXCEPTION 'approval posted twice'; END IF;
    END;
    IF result->>'po_status'<>'received' OR
       result->>'inventory_posted'<>'true' OR
       result->>'accounting_reconciled'<>'false' THEN
      RAISE EXCEPTION 'receipt was not posted with honest accounting state';
    END IF;
    replay:= receive_warehouse_purchase_order(
      a,'PO-E2E-1200','REC-1',receipt_lines,receiver,reviewer);
    IF replay->>'replayed'<>'true' OR
      replay->>'receipt_id'<>result->>'receipt_id' THEN
      RAISE EXCEPTION 'duplicate receipt replay was not stable';
    END IF;
    SELECT quantity INTO q FROM stock_balances
      WHERE company_id=a AND sku='TEST-WINE' AND bucket='available';
    IF q<>1180 THEN RAISE EXCEPTION 'expected 1180 available cases, got %',q; END IF;
    SELECT quantity INTO q FROM stock_balances
      WHERE company_id=a AND sku='TEST-WINE' AND bucket='damaged';
    IF q<>20 THEN RAISE EXCEPTION 'expected 20 damaged cases, got %',q; END IF;
    SELECT count(*) INTO n FROM stock_movements
      WHERE company_id=a AND source_ref='PO-E2E-1200';
    IF n<>2 THEN RAISE EXCEPTION 'receipt posted % movements instead of 2',n; END IF;

    bad:=FALSE;
    BEGIN
      PERFORM receive_warehouse_purchase_order(
        a,'PO-E2E-1200','REC-1',
        '[{"sku":"TEST-WINE","accepted_qty":1200}]'::jsonb,
        receiver,reviewer);
    EXCEPTION WHEN unique_violation THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'receipt key payload substitution allowed'; END IF;
    bad:=FALSE;
    BEGIN
      PERFORM receive_warehouse_purchase_order(
        a,'PO-E2E-1200','REC-2',
        '[{"sku":"TEST-WINE","accepted_qty":1}]'::jsonb,
        receiver,reviewer);
    EXCEPTION WHEN check_violation THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'over-receipt accepted'; END IF;

    SELECT count(*) INTO n FROM warehouse_receipt_documents WHERE company_id=a;
    IF n<>1 THEN RAISE EXCEPTION 'failure left extra receipt documents'; END IF;
    SELECT qty_received INTO q FROM purchasing_order_lines
      WHERE company_id=a AND po_id=v_po AND sku='TEST-WINE';
    IF q<>1200 THEN RAISE EXCEPTION 'quantity counter corrupted'; END IF;

    bad:=FALSE;
    BEGIN
      PERFORM create_warehouse_purchase_order(
        a,'PO-CROSS','SUP-1',wb,
        '[{"sku":"TEST-WINE","qty_ordered":1}]'::jsonb,creator);
    EXCEPTION WHEN foreign_key_violation THEN bad:=TRUE;
    END;
    IF NOT bad THEN RAISE EXCEPTION 'cross-tenant warehouse permitted'; END IF;
END
$test$;

DO $perms$
BEGIN
 IF has_function_privilege('authenticated',
      'receive_warehouse_purchase_order(uuid,text,text,jsonb,uuid,uuid)',
      'EXECUTE') OR
    has_function_privilege('service_role',
      'receive_warehouse_purchase_order(uuid,text,text,jsonb,uuid,uuid)',
      'EXECUTE') OR
    has_table_privilege('service_role','purchasing_orders','INSERT') OR
    has_table_privilege('service_role','warehouse_receipt_documents','INSERT')
 THEN RAISE EXCEPTION 'direct client mutation bypass possible'; END IF;
END
$perms$;

SELECT set_config('request.jwt.claims',
  jsonb_build_object('company_id',company_a)::text,false)
  FROM po_acceptance_ids;
SET ROLE authenticated;
DO $rls$
DECLARE n INTEGER;
BEGIN
 SELECT count(*) INTO n FROM purchasing_orders;
 IF n<>1 THEN RAISE EXCEPTION 'company A cannot isolate purchase orders: %',n; END IF;
 SELECT count(*) INTO n FROM warehouse_receipt_documents;
 IF n<>1 THEN RAISE EXCEPTION 'company A cannot isolate receipt documents: %',n; END IF;
 SELECT count(*) INTO n FROM staged_warehouse_receipts;
 IF n<>1 THEN RAISE EXCEPTION 'company A cannot isolate receipt approvals: %',n; END IF;
END
$rls$;
RESET ROLE;
SELECT set_config('request.jwt.claims',
  jsonb_build_object('company_id',company_b)::text,false)
  FROM po_acceptance_ids;
SET ROLE authenticated;
DO $rls$
DECLARE n INTEGER;
BEGIN
 SELECT count(*) INTO n FROM purchasing_orders;
 IF n<>0 THEN RAISE EXCEPTION 'cross-tenant purchasing read leaked'; END IF;
 SELECT count(*) INTO n FROM warehouse_receipt_lines;
 IF n<>0 THEN RAISE EXCEPTION 'cross-tenant receipt read leaked'; END IF;
 SELECT count(*) INTO n FROM staged_warehouse_receipts;
 IF n<>0 THEN RAISE EXCEPTION 'cross-tenant receipt approval leaked'; END IF;
END
$rls$;
RESET ROLE;
ROLLBACK;
\echo 'PASS: draft -> independent approval -> inspected receipt -> stock, replay, RLS'
