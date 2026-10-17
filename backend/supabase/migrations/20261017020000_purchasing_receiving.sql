-- SecretaryAI V6: durable PO approval -> inspected receipt -> exact-once inventory.
-- Manual schema path; equivalent copies exist in both Supabase migration roots.
-- Requires V5 stock-ledger schema first. This is NOT a QuickBooks sync.

CREATE TABLE IF NOT EXISTS purchasing_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    po_number TEXT NOT NULL CHECK (length(btrim(po_number)) BETWEEN 1 AND 100),
    supplier_ref TEXT NOT NULL CHECK (length(btrim(supplier_ref)) > 0),
    warehouse_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
      CHECK (status IN ('draft','approved','partially_received','received')),
    created_by UUID NOT NULL REFERENCES users(id),
    approved_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    UNIQUE(company_id,po_number),
    FOREIGN KEY(company_id,warehouse_id) REFERENCES stock_warehouses(company_id,id)
);
CREATE TABLE IF NOT EXISTS purchasing_order_lines (
    company_id UUID NOT NULL,
    po_id UUID NOT NULL,
    sku TEXT NOT NULL,
    qty_ordered NUMERIC(18,3) NOT NULL CHECK(qty_ordered > 0),
    qty_received NUMERIC(18,3) NOT NULL DEFAULT 0
      CHECK(qty_received >= 0 AND qty_received <= qty_ordered),
    PRIMARY KEY(company_id,po_id,sku),
    FOREIGN KEY(company_id,po_id) REFERENCES purchasing_orders(company_id,id),
    FOREIGN KEY(company_id,sku) REFERENCES stock_skus(company_id,sku)
);
CREATE TABLE IF NOT EXISTS warehouse_receipt_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    po_id UUID NOT NULL,
    receipt_key TEXT NOT NULL CHECK(length(btrim(receipt_key)) BETWEEN 1 AND 150),
    payload JSONB NOT NULL,
    received_by UUID NOT NULL REFERENCES users(id),
    approved_by UUID NOT NULL REFERENCES users(id),
    posted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    UNIQUE(company_id,receipt_key),
    FOREIGN KEY(company_id,po_id) REFERENCES purchasing_orders(company_id,id),
    CHECK(received_by <> approved_by)
);
CREATE TABLE IF NOT EXISTS warehouse_receipt_lines (
    company_id UUID NOT NULL,
    receipt_id UUID NOT NULL,
    line_number INTEGER NOT NULL CHECK(line_number > 0),
    sku TEXT NOT NULL,
    lot_code TEXT NOT NULL DEFAULT '',
    accepted_qty NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK(accepted_qty >= 0),
    damaged_qty NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK(damaged_qty >= 0),
    quarantined_qty NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK(quarantined_qty >= 0),
    PRIMARY KEY(company_id,receipt_id,line_number),
    FOREIGN KEY(company_id,receipt_id) REFERENCES warehouse_receipt_documents(company_id,id),
    FOREIGN KEY(company_id,sku) REFERENCES stock_skus(company_id,sku),
    CHECK(accepted_qty + damaged_qty + quarantined_qty > 0)
);
CREATE INDEX IF NOT EXISTS idx_po_company_status ON purchasing_orders(company_id,status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_receipts_po ON warehouse_receipt_documents(company_id,po_id,posted_at DESC);

ALTER TABLE purchasing_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchasing_order_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE warehouse_receipt_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE warehouse_receipt_lines ENABLE ROW LEVEL SECURITY;
CREATE POLICY po_tenant_read ON purchasing_orders FOR SELECT
    USING(company_id = (auth.jwt()->>'company_id')::UUID);
CREATE POLICY po_lines_tenant_read ON purchasing_order_lines FOR SELECT
    USING(company_id = (auth.jwt()->>'company_id')::UUID);
CREATE POLICY receipt_tenant_read ON warehouse_receipt_documents FOR SELECT
    USING(company_id = (auth.jwt()->>'company_id')::UUID);
CREATE POLICY receipt_lines_tenant_read ON warehouse_receipt_lines FOR SELECT
    USING(company_id = (auth.jwt()->>'company_id')::UUID);
REVOKE ALL ON purchasing_orders,purchasing_order_lines,
    warehouse_receipt_documents,warehouse_receipt_lines
    FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON purchasing_orders,purchasing_order_lines,
    warehouse_receipt_documents,warehouse_receipt_lines
    TO authenticated,service_role;

-- Only the protected RPC may create a draft and its immutable ordered lines.
CREATE OR REPLACE FUNCTION create_warehouse_purchase_order(
    p_company_id UUID, p_po_number TEXT, p_supplier_ref TEXT,
    p_warehouse_id UUID, p_lines JSONB, p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,public,pg_temp AS $createpo$
DECLARE
    v_id UUID;
    v_item JSONB;
    v_sku TEXT;
    v_qty NUMERIC;
    v_count INTEGER := 0;
BEGIN
    IF p_company_id IS NULL OR p_actor IS NULL OR
       p_po_number IS NULL OR length(btrim(p_po_number)) NOT BETWEEN 1 AND 100 OR
       p_supplier_ref IS NULL OR btrim(p_supplier_ref) = '' OR
       p_lines IS NULL OR jsonb_typeof(p_lines) <> 'array' OR
       jsonb_array_length(p_lines) NOT BETWEEN 1 AND 100
    THEN
        RAISE EXCEPTION 'invalid purchase order' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
                  AND is_active AND role IN('owner','manager','back_office')) THEN
        RAISE EXCEPTION 'unauthorized PO creator' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stock_warehouses
                  WHERE company_id=p_company_id AND id=p_warehouse_id) THEN
        RAISE EXCEPTION 'warehouse not part of tenant' USING ERRCODE='23503';
    END IF;
    INSERT INTO purchasing_orders(
      company_id,po_number,supplier_ref,warehouse_id,created_by
    ) VALUES (
      p_company_id,btrim(p_po_number),btrim(p_supplier_ref),p_warehouse_id,p_actor
    ) RETURNING id INTO v_id;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_lines) LOOP
        IF jsonb_typeof(v_item) <> 'object' THEN
            RAISE EXCEPTION 'invalid PO line' USING ERRCODE='22023';
        END IF;
        v_sku := nullif(btrim(v_item->>'sku'),'');
        v_qty := (v_item->>'qty_ordered')::NUMERIC;
        IF v_sku IS NULL OR v_qty IS NULL OR v_qty <= 0 OR
           v_qty = 'NaN'::numeric OR v_qty <> round(v_qty,3) OR
           NOT EXISTS(SELECT 1 FROM stock_skus WHERE company_id=p_company_id
                      AND sku=v_sku AND base_unit='case')
        THEN
            RAISE EXCEPTION 'invalid case SKU or ordered quantity' USING ERRCODE='22023';
        END IF;
        INSERT INTO purchasing_order_lines(company_id,po_id,sku,qty_ordered)
        VALUES(p_company_id,v_id,v_sku,v_qty);
        v_count := v_count + 1;
    END LOOP;
    RETURN jsonb_build_object('po_id',v_id,'po_number',btrim(p_po_number),
        'status','draft','line_count',v_count,'inventory_posted',FALSE);
END
$createpo$;

CREATE OR REPLACE FUNCTION approve_warehouse_purchase_order(
    p_company_id UUID,p_po_number TEXT,p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,public,pg_temp AS $approvepo$
DECLARE v_order purchasing_orders%ROWTYPE;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
                  AND is_active AND role IN('owner','manager')) THEN
        RAISE EXCEPTION 'unauthorized PO approver' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_order FROM purchasing_orders WHERE company_id=p_company_id
      AND po_number=p_po_number FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'PO not found' USING ERRCODE='P0002'; END IF;
    IF v_order.status <> 'draft' OR v_order.created_by = p_actor THEN
        RAISE EXCEPTION 'PO must be draft and independently approved'
          USING ERRCODE='23514';
    END IF;
    UPDATE purchasing_orders SET status='approved',approved_by=p_actor,
      approved_at=now(),updated_at=now() WHERE id=v_order.id;
    RETURN jsonb_build_object('po_id',v_order.id,'status','approved',
      'inventory_posted',FALSE);
END
$approvepo$;

-- Inspected receipt must have separate receiver and approver, both active.
-- PO row lock serializes concurrent receipts. All stock movements, line
-- counters and receipt documents commit or roll back as ONE database action.
CREATE OR REPLACE FUNCTION receive_warehouse_purchase_order(
    p_company_id UUID,p_po_number TEXT,p_receipt_key TEXT,p_lines JSONB,
    p_receiver UUID,p_approver UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,public,pg_temp AS $receivepo$
DECLARE
    v_po purchasing_orders%ROWTYPE;
    v_previous warehouse_receipt_documents%ROWTYPE;
    v_receipt_id UUID;
    v_line JSONB;
    v_line_no INTEGER := 0;
    v_sku TEXT;
    v_lot TEXT;
    v_good NUMERIC;
    v_damaged NUMERIC;
    v_quarantine NUMERIC;
    v_total NUMERIC;
    v_pending INTEGER;
    v_move JSONB;
    v_key TEXT;
    v_kind TEXT;
    v_qty NUMERIC;
    v_new_status TEXT;
BEGIN
    IF p_receipt_key IS NULL OR length(btrim(p_receipt_key)) NOT BETWEEN 1 AND 150 OR
       p_lines IS NULL OR jsonb_typeof(p_lines) <> 'array' OR
       jsonb_array_length(p_lines) NOT BETWEEN 1 AND 200
    THEN RAISE EXCEPTION 'invalid receipt' USING ERRCODE='22023'; END IF;
    IF p_receiver IS NULL OR p_approver IS NULL OR p_receiver = p_approver OR
       NOT EXISTS(SELECT 1 FROM users WHERE id=p_receiver AND company_id=p_company_id
                 AND is_active AND role IN('owner','manager','back_office')) OR
       NOT EXISTS(SELECT 1 FROM users WHERE id=p_approver AND company_id=p_company_id
                 AND is_active AND role IN('owner','manager'))
    THEN RAISE EXCEPTION 'receipt requires independent authorized approval'
         USING ERRCODE='42501'; END IF;

    SELECT * INTO v_po FROM purchasing_orders
       WHERE company_id=p_company_id AND po_number=p_po_number FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'approved PO not found' USING ERRCODE='P0002'; END IF;
    SELECT * INTO v_previous FROM warehouse_receipt_documents
      WHERE company_id=p_company_id AND receipt_key=btrim(p_receipt_key);
    IF FOUND THEN
      IF v_previous.po_id <> v_po.id OR v_previous.payload <> p_lines OR
         v_previous.received_by <> p_receiver OR
         v_previous.approved_by <> p_approver THEN
          RAISE EXCEPTION 'receipt key already used with different payload'
            USING ERRCODE='23505';
      END IF;
      RETURN jsonb_build_object('receipt_id',v_previous.id,
        'replayed',TRUE,'inventory_posted',TRUE,'accounting_reconciled',FALSE);
    END IF;
    IF v_po.status NOT IN('approved','partially_received') THEN
        RAISE EXCEPTION 'purchase order not approved for receiving'
            USING ERRCODE='23514';
    END IF;

    INSERT INTO warehouse_receipt_documents(
      company_id,po_id,receipt_key,payload,received_by,approved_by
    ) VALUES(
      p_company_id,v_po.id,btrim(p_receipt_key),p_lines,p_receiver,p_approver
    ) RETURNING id INTO v_receipt_id;
    FOR v_line IN SELECT value FROM jsonb_array_elements(p_lines) LOOP
        v_line_no := v_line_no+1;
        IF jsonb_typeof(v_line) <> 'object' THEN
            RAISE EXCEPTION 'receipt line must be object' USING ERRCODE='22023';
        END IF;
        v_sku := nullif(btrim(v_line->>'sku'),'');
        v_lot := btrim(coalesce(v_line->>'lot_code',''));
        v_good := coalesce((v_line->>'accepted_qty')::NUMERIC,0);
        v_damaged := coalesce((v_line->>'damaged_qty')::NUMERIC,0);
        v_quarantine := coalesce((v_line->>'quarantined_qty')::NUMERIC,0);
        v_total := v_good+v_damaged+v_quarantine;
        IF v_sku IS NULL OR v_total <= 0 OR
           v_good = 'NaN'::numeric OR v_damaged = 'NaN'::numeric OR
           v_quarantine = 'NaN'::numeric OR
           v_good < 0 OR v_damaged < 0 OR v_quarantine < 0 OR
           v_good <> round(v_good,3) OR
           v_damaged <> round(v_damaged,3) OR
           v_quarantine <> round(v_quarantine,3) OR
           length(v_lot) > 120 THEN
             RAISE EXCEPTION 'invalid receipt line quantities'
                 USING ERRCODE='22023';
        END IF;
        UPDATE purchasing_order_lines SET qty_received=qty_received+v_total
          WHERE company_id=p_company_id AND po_id=v_po.id AND sku=v_sku
            AND qty_received+v_total <= qty_ordered;
        IF NOT FOUND THEN RAISE EXCEPTION 'unrecognized SKU or overreceipt'
            USING ERRCODE='23514'; END IF;
        INSERT INTO warehouse_receipt_lines(
          company_id,receipt_id,line_number,sku,lot_code,
          accepted_qty,damaged_qty,quarantined_qty
        ) VALUES(
          p_company_id,v_receipt_id,v_line_no,v_sku,v_lot,
          v_good,v_damaged,v_quarantine
        );
        -- Positive ledger entries are independent stock buckets. No implicit
        -- release of damaged or quarantined cases into available inventory.
        FOR v_kind,v_qty IN
          SELECT 'available',v_good UNION ALL SELECT 'damaged',v_damaged
          UNION ALL SELECT 'quarantined',v_quarantine
        LOOP
          IF v_qty > 0 THEN
            v_key := 'receipt:' || v_receipt_id::text || ':' ||
              v_line_no::TEXT || ':' || v_kind;
            v_move := post_stock_movement(
              p_company_id,v_sku,v_po.warehouse_id,v_lot,v_kind,'case',v_qty,
              'warehouse_receipt',v_po.po_number,v_key,p_receiver
            );
          END IF;
        END LOOP;
    END LOOP;
    SELECT count(*) INTO v_pending FROM purchasing_order_lines
      WHERE company_id=p_company_id AND po_id=v_po.id
      AND qty_received < qty_ordered;
    v_new_status := CASE WHEN v_pending=0 THEN 'received'
                    ELSE 'partially_received' END;
    UPDATE purchasing_orders SET status=v_new_status,updated_at=now()
      WHERE id=v_po.id;
    RETURN jsonb_build_object('receipt_id',v_receipt_id,
      'po_id',v_po.id,'po_status',v_new_status,'replayed',FALSE,
      'inventory_posted',TRUE,'accounting_reconciled',FALSE);
END
$receivepo$;

REVOKE ALL ON FUNCTION create_warehouse_purchase_order(UUID,TEXT,TEXT,UUID,JSONB,UUID)
  FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION approve_warehouse_purchase_order(UUID,TEXT,UUID)
  FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION receive_warehouse_purchase_order(UUID,TEXT,TEXT,JSONB,UUID,UUID)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION create_warehouse_purchase_order(UUID,TEXT,TEXT,UUID,JSONB,UUID)
  TO service_role;
GRANT EXECUTE ON FUNCTION approve_warehouse_purchase_order(UUID,TEXT,UUID)
  TO service_role;
GRANT EXECUTE ON FUNCTION receive_warehouse_purchase_order(UUID,TEXT,TEXT,JSONB,UUID,UUID)
  TO service_role;

-- Receipt submission and approval are two authenticated sessions. Neither
-- the browser nor the receiving employee can supply the approver identity.
CREATE TABLE IF NOT EXISTS staged_warehouse_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    po_id UUID NOT NULL,
    receipt_key TEXT NOT NULL CHECK(length(btrim(receipt_key)) BETWEEN 1 AND 150),
    payload JSONB NOT NULL,
    submitted_by UUID NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','posted')),
    posted_receipt_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    UNIQUE(company_id,id),UNIQUE(company_id,receipt_key),
    FOREIGN KEY(company_id,po_id) REFERENCES purchasing_orders(company_id,id),
    FOREIGN KEY(company_id,posted_receipt_id)
      REFERENCES warehouse_receipt_documents(company_id,id)
);
CREATE INDEX IF NOT EXISTS idx_staged_receipts_pending
  ON staged_warehouse_receipts(company_id,status,created_at DESC);
ALTER TABLE staged_warehouse_receipts ENABLE ROW LEVEL SECURITY;
CREATE POLICY staged_receipts_tenant_read ON staged_warehouse_receipts FOR SELECT
  USING(company_id=(auth.jwt()->>'company_id')::UUID);
REVOKE ALL ON staged_warehouse_receipts FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON staged_warehouse_receipts TO authenticated,service_role;

CREATE OR REPLACE FUNCTION stage_warehouse_receipt(
    p_company_id UUID,p_po_number TEXT,p_receipt_key TEXT,
    p_lines JSONB,p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $stagereceipt$
DECLARE v_po purchasing_orders%ROWTYPE;
        v_previous staged_warehouse_receipts%ROWTYPE;
        v_id UUID;
BEGIN
    IF p_receipt_key IS NULL OR length(btrim(p_receipt_key)) NOT BETWEEN 1 AND 150
      OR p_lines IS NULL OR jsonb_typeof(p_lines)<>'array'
      OR jsonb_array_length(p_lines) NOT BETWEEN 1 AND 200
    THEN RAISE EXCEPTION 'invalid staged receipt' USING ERRCODE='22023'; END IF;
    IF NOT EXISTS(SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
                  AND is_active AND role IN('owner','manager','back_office'))
    THEN RAISE EXCEPTION 'unauthorized receiving employee' USING ERRCODE='42501'; END IF;
    SELECT * INTO v_po FROM purchasing_orders
      WHERE company_id=p_company_id AND po_number=p_po_number FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'PO not found' USING ERRCODE='P0002'; END IF;
    SELECT * INTO v_previous FROM staged_warehouse_receipts
      WHERE company_id=p_company_id AND receipt_key=btrim(p_receipt_key);
    IF FOUND THEN
      IF v_previous.po_id<>v_po.id OR v_previous.payload<>p_lines OR
         v_previous.submitted_by<>p_actor THEN
         RAISE EXCEPTION 'receipt key already in use'
           USING ERRCODE='23505';
      END IF;
      RETURN jsonb_build_object('staged_receipt_id',v_previous.id,
        'status',v_previous.status,'replayed',TRUE,'inventory_posted',
        v_previous.status='posted');
    END IF;
    IF v_po.status NOT IN('approved','partially_received') THEN
       RAISE EXCEPTION 'purchase order not open for receipt'
         USING ERRCODE='23514';
    END IF;
    INSERT INTO staged_warehouse_receipts(
      company_id,po_id,receipt_key,payload,submitted_by
    ) VALUES (
      p_company_id,v_po.id,btrim(p_receipt_key),p_lines,p_actor
    ) RETURNING id INTO v_id;
    RETURN jsonb_build_object('staged_receipt_id',v_id,
      'status','pending','replayed',FALSE,'inventory_posted',FALSE);
END
$stagereceipt$;

CREATE OR REPLACE FUNCTION approve_staged_warehouse_receipt(
    p_company_id UUID,p_staged_receipt_id UUID,p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $approve_receipt$
DECLARE v_stage staged_warehouse_receipts%ROWTYPE;
        v_po purchasing_orders%ROWTYPE;
        v_result JSONB;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
                  AND is_active AND role IN('owner','manager')) THEN
       RAISE EXCEPTION 'unauthorized receipt approver'
         USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_stage FROM staged_warehouse_receipts
      WHERE company_id=p_company_id AND id=p_staged_receipt_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'receipt draft not found'
      USING ERRCODE='P0002'; END IF;
    IF v_stage.submitted_by=p_actor THEN
       RAISE EXCEPTION 'receipt requires independent approval'
         USING ERRCODE='42501';
    END IF;
    IF v_stage.status='posted' THEN
       RETURN jsonb_build_object('staged_receipt_id',v_stage.id,
         'receipt_id',v_stage.posted_receipt_id,'replayed',TRUE,
         'inventory_posted',TRUE,'accounting_reconciled',FALSE);
    END IF;
    SELECT * INTO v_po FROM purchasing_orders
      WHERE company_id=p_company_id AND id=v_stage.po_id;
    v_result:=receive_warehouse_purchase_order(
      p_company_id,v_po.po_number,v_stage.receipt_key,
      v_stage.payload,v_stage.submitted_by,p_actor
    );
    UPDATE staged_warehouse_receipts SET status='posted',
      approved_at=now(),posted_receipt_id=(v_result->>'receipt_id')::UUID
      WHERE id=v_stage.id;
    RETURN v_result || jsonb_build_object(
      'staged_receipt_id',v_stage.id,'status','posted'
    );
END
$approve_receipt$;

-- The previous service-executable receive RPC accepted a caller-provided
-- approver UUID; make it internal-only. The public service boundary is now
-- stage_warehouse_receipt -> approve_staged_warehouse_receipt.
REVOKE ALL ON FUNCTION receive_warehouse_purchase_order(
  UUID,TEXT,TEXT,JSONB,UUID,UUID
) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION stage_warehouse_receipt(UUID,TEXT,TEXT,JSONB,UUID)
  FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION approve_staged_warehouse_receipt(UUID,UUID,UUID)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION stage_warehouse_receipt(UUID,TEXT,TEXT,JSONB,UUID)
  TO service_role;
GRANT EXECUTE ON FUNCTION approve_staged_warehouse_receipt(UUID,UUID,UUID)
  TO service_role;
