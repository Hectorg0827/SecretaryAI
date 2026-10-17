-- Real PostgreSQL acceptance gate, executed after schema migrations in CI.
-- Transaction rolls back test companies so it can run without persistent data.
\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE stock_ledger_test_ids (
    company_a UUID, company_b UUID, warehouse_a UUID,
    warehouse_b UUID, actor_a UUID, actor_b UUID
);

DO $test$
DECLARE
    a UUID;
    b UUID;
    wa UUID;
    wb UUID;
    ua UUID;
    ub UUID;
    first_event JSONB;
    replay_event JSONB;
    bad_event BOOLEAN;
    n NUMERIC;
    events_count INTEGER;
    stock_count NUMERIC;
    damaged_count NUMERIC;
BEGIN
    INSERT INTO companies(name) VALUES ('Stock Ledger Test A') RETURNING id INTO a;
    INSERT INTO companies(name) VALUES ('Stock Ledger Test B') RETURNING id INTO b;
    INSERT INTO users(company_id,email,hashed_password,role)
      VALUES (a, 'ledger-a-'||a::text||'@example.test','test-only','manager')
      RETURNING id INTO ua;
    INSERT INTO users(company_id,email,hashed_password,role)
      VALUES (b, 'ledger-b-'||b::text||'@example.test','test-only','owner')
      RETURNING id INTO ub;
    INSERT INTO stock_skus(company_id,sku,product_name,base_unit)
      VALUES (a,'CASES-1','Test Cases','case'),
             (b,'CASES-1','Test Cases','case');
    INSERT INTO stock_warehouses(company_id,code,name)
      VALUES (a,'WH-1','A warehouse') RETURNING id INTO wa;
    INSERT INTO stock_warehouses(company_id,code,name)
      VALUES (b,'WH-1','B warehouse') RETURNING id INTO wb;

    INSERT INTO stock_ledger_test_ids VALUES(a,b,wa,wb,ua,ub);

    -- 1,200 ordered; 1,180 accepted and 20 physically damaged.
    first_event := post_stock_movement(
      a,'CASES-1',wa,'LOT-1','available','case',1180,
      'warehouse_receipt','PO-1200','receipt-1200:available',ua
    );
    IF first_event->>'replayed' <> 'false' THEN
      RAISE EXCEPTION 'first receipt was incorrectly marked replayed';
    END IF;
    replay_event := post_stock_movement(
      a,'CASES-1',wa,'LOT-1','available','case',1180,
      'warehouse_receipt','PO-1200','receipt-1200:available',ua
    );
    IF replay_event->>'replayed' <> 'true' OR
       first_event->>'movement_id' <> replay_event->>'movement_id' THEN
      RAISE EXCEPTION 'replayed receipt was not idempotent';
    END IF;
    PERFORM post_stock_movement(
      a,'CASES-1',wa,'LOT-1','damaged','case',20,
      'warehouse_receipt','PO-1200','receipt-1200:damaged',ua
    );

    SELECT quantity INTO stock_count FROM stock_balances
      WHERE company_id=a AND sku='CASES-1' AND bucket='available';
    SELECT quantity INTO damaged_count FROM stock_balances
      WHERE company_id=a AND sku='CASES-1' AND bucket='damaged';
    IF stock_count <> 1180 OR damaged_count <> 20 THEN
      RAISE EXCEPTION 'receipt quantities incorrect: available %, damaged %',
        stock_count, damaged_count;
    END IF;
    SELECT count(*) INTO events_count FROM stock_movements WHERE company_id=a;
    IF events_count <> 2 THEN
      RAISE EXCEPTION 'replayed inventory event posted twice';
    END IF;

    -- Replay with changed payload MUST fail rather than silently use a key.
    bad_event := FALSE;
    BEGIN
      PERFORM post_stock_movement(
        a,'CASES-1',wa,'LOT-1','available','case',1181,
        'warehouse_receipt','PO-1200','receipt-1200:available',ua
      );
    EXCEPTION WHEN unique_violation THEN bad_event := TRUE;
    END;
    IF NOT bad_event THEN
      RAISE EXCEPTION 'changed-payload replay was permitted';
    END IF;

    -- Same SKU in another tenant must not allow cross-company warehouse use.
    bad_event := FALSE;
    BEGIN
      PERFORM post_stock_movement(
        a,'CASES-1',wb,'LOT-1','available','case',50,
        'warehouse_receipt','PO-OTHER','wrong-tenant-warehouse',ua
      );
    EXCEPTION WHEN foreign_key_violation THEN bad_event := TRUE;
    END;
    IF NOT bad_event THEN
      RAISE EXCEPTION 'cross-company warehouse was accepted';
    END IF;

    bad_event := FALSE;
    BEGIN
      PERFORM post_stock_movement(
        a,'CASES-1',wa,'LOT-1','available','case',50,
        'warehouse_receipt','PO-OTHER','wrong-tenant-actor',ub
      );
    EXCEPTION WHEN insufficient_privilege THEN bad_event := TRUE;
    END;
    IF NOT bad_event THEN
      RAISE EXCEPTION 'cross-company actor was accepted';
    END IF;

    -- Cannot ship more than available; negative attempted movement rolls back.
    bad_event := FALSE;
    BEGIN
      PERFORM post_stock_movement(
        a,'CASES-1',wa,'LOT-1','available','case',-1181,
        'shipment','SHIP-1','shipment-overdraw',ua
      );
    EXCEPTION WHEN check_violation THEN bad_event := TRUE;
    END;
    IF NOT bad_event THEN
      RAISE EXCEPTION 'negative available inventory permitted';
    END IF;
    SELECT quantity INTO n FROM stock_balances
      WHERE company_id=a AND sku='CASES-1' AND bucket='available';
    IF n <> 1180 THEN
      RAISE EXCEPTION 'failed movement changed the balance';
    END IF;

    -- A successful shipment updates the same durable balance.
    PERFORM post_stock_movement(
      a,'CASES-1',wa,'LOT-1','available','case',-30,
      'shipment','SHIP-2','shipment-30',ua
    );
    SELECT quantity INTO n FROM stock_balances
      WHERE company_id=a AND sku='CASES-1' AND bucket='available';
    IF n <> 1150 THEN
      RAISE EXCEPTION 'shipment not applied exactly once';
    END IF;

    -- Posted history cannot be mutated; compensating events are required.
    bad_event := FALSE;
    BEGIN
      UPDATE stock_movements SET quantity_delta=1
        WHERE id=(first_event->>'movement_id')::UUID;
    EXCEPTION WHEN check_violation THEN bad_event := TRUE;
    END;
    IF NOT bad_event THEN
      RAISE EXCEPTION 'immutable inventory ledger was modified';
    END IF;

    -- Tenant B may reuse the same event key without touching A's balances.
    PERFORM post_stock_movement(
      b,'CASES-1',wb,'LOT-1','available','case',7,
      'opening_count','COUNT-B','receipt-1200:available',ub
    );
    SELECT quantity INTO n FROM stock_balances
      WHERE company_id=b AND sku='CASES-1' AND bucket='available';
    IF n <> 7 THEN
      RAISE EXCEPTION 'tenant B stock count incorrect';
    END IF;
END
$test$;

-- Database permissions deny browser/API users stock posting even when
-- they supply another company's UUID in a request.
DO $perms$
BEGIN
  IF has_table_privilege('service_role', 'stock_movements', 'INSERT') OR
     has_table_privilege('service_role', 'stock_balances', 'UPDATE')
  THEN
    RAISE EXCEPTION 'service role can bypass atomic stock posting';
  END IF;
  IF has_function_privilege(
    'authenticated',
    'post_stock_movement(uuid,text,uuid,text,text,text,numeric,text,text,text,uuid)',
    'EXECUTE'
  ) THEN
    RAISE EXCEPTION 'browser role can invoke the privileged stock RPC';
  END IF;
END
$perms$;

-- Test actual RLS filters, not just the existence of RLS metadata.
SELECT set_config(
  'request.jwt.claims',
  jsonb_build_object('company_id', company_a)::TEXT,
  FALSE
) FROM stock_ledger_test_ids;

SET ROLE authenticated;
DO $rls$
DECLARE n INTEGER;
BEGIN
  SELECT count(*) INTO n FROM stock_movements;
  IF n <> 3 THEN
    RAISE EXCEPTION 'tenant A should see 3 stock events, got %', n;
  END IF;
  SELECT count(*) INTO n FROM stock_balances;
  IF n <> 2 THEN
    RAISE EXCEPTION 'tenant A should see 2 buckets, got %', n;
  END IF;
END
$rls$;
RESET ROLE;

SELECT set_config(
  'request.jwt.claims',
  jsonb_build_object('company_id', company_b)::TEXT,
  FALSE
) FROM stock_ledger_test_ids;
SET ROLE authenticated;
DO $rls$
DECLARE n INTEGER;
BEGIN
  SELECT count(*) INTO n FROM stock_movements;
  IF n <> 1 THEN
    RAISE EXCEPTION 'tenant B should see only its own event, got %', n;
  END IF;
END
$rls$;
RESET ROLE;

ROLLBACK;
\echo 'PASS: exact-once stock, damaged bucket, nonnegative balance, immutable log, tenant ACL'
