-- SecretaryAI V5: Internal stock-ledger foundation.
-- Apply ONCE through the active migration mechanism, after schema_v4.
-- Do not expose direct inventory posting to frontend roles or AI tools.
-- Book balances only for events posted through this ledger. Historical QB
-- snapshots are not imported or added to these quantities automatically.

CREATE TABLE IF NOT EXISTS stock_skus (
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sku TEXT NOT NULL CHECK (length(btrim(sku)) > 0),
    product_name TEXT NOT NULL,
    base_unit TEXT NOT NULL CHECK (base_unit IN ('case','unit','pallet')),
    qb_item_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, sku)
);

CREATE TABLE IF NOT EXISTS stock_warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    code TEXT NOT NULL CHECK (length(btrim(code)) > 0),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, id),
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS stock_balances (
    company_id UUID NOT NULL,
    sku TEXT NOT NULL,
    warehouse_id UUID NOT NULL,
    lot_code TEXT NOT NULL DEFAULT '',
    bucket TEXT NOT NULL CHECK (bucket IN ('available','damaged','quarantined')),
    unit TEXT NOT NULL CHECK (unit IN ('case','unit','pallet')),
    quantity NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, sku, warehouse_id, lot_code, bucket, unit),
    FOREIGN KEY (company_id, sku) REFERENCES stock_skus(company_id, sku),
    FOREIGN KEY (company_id, warehouse_id) REFERENCES stock_warehouses(company_id, id)
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    sku TEXT NOT NULL,
    warehouse_id UUID NOT NULL,
    lot_code TEXT NOT NULL DEFAULT '',
    bucket TEXT NOT NULL CHECK (bucket IN ('available','damaged','quarantined')),
    unit TEXT NOT NULL CHECK (unit IN ('case','unit','pallet')),
    quantity_delta NUMERIC(18,3) NOT NULL CHECK (quantity_delta <> 0),
    source_type TEXT NOT NULL CHECK (source_type IN (
        'opening_count','warehouse_receipt','shipment','transfer',
        'approved_adjustment','return','quarantine_release'
    )),
    source_ref TEXT NOT NULL CHECK (length(btrim(source_ref)) > 0),
    event_key TEXT NOT NULL CHECK (length(btrim(event_key)) BETWEEN 1 AND 200),
    actor_user_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, event_key),
    FOREIGN KEY (company_id, sku) REFERENCES stock_skus(company_id, sku),
    FOREIGN KEY (company_id, warehouse_id) REFERENCES stock_warehouses(company_id, id),
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_company_time
    ON stock_movements(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_movements_source
    ON stock_movements(company_id, source_type, source_ref);

-- An immutable ledger: rectify accounting mistakes with a compensating
-- movement and a separately approved reason; never rewrite past postings.
CREATE OR REPLACE FUNCTION reject_stock_movement_rewrite()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'posted stock movements are immutable' USING ERRCODE='23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_stock_movements_immutable ON stock_movements;
CREATE TRIGGER trg_stock_movements_immutable
    BEFORE UPDATE OR DELETE ON stock_movements
    FOR EACH ROW EXECUTE FUNCTION reject_stock_movement_rewrite();

ALTER TABLE stock_skus ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_warehouses ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_movements ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_stock_skus ON stock_skus;
CREATE POLICY tenant_stock_skus ON stock_skus
    USING (company_id = (auth.jwt() ->> 'company_id')::UUID)
    WITH CHECK (company_id = (auth.jwt() ->> 'company_id')::UUID);
DROP POLICY IF EXISTS tenant_stock_warehouses ON stock_warehouses;
CREATE POLICY tenant_stock_warehouses ON stock_warehouses
    USING (company_id = (auth.jwt() ->> 'company_id')::UUID)
    WITH CHECK (company_id = (auth.jwt() ->> 'company_id')::UUID);
DROP POLICY IF EXISTS tenant_stock_balances ON stock_balances;
CREATE POLICY tenant_stock_balances ON stock_balances
    FOR SELECT USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
DROP POLICY IF EXISTS tenant_stock_movements ON stock_movements;
CREATE POLICY tenant_stock_movements ON stock_movements
    FOR SELECT USING (company_id = (auth.jwt() ->> 'company_id')::UUID);

-- The authenticated role can only READ company-scoped stock. All inserts,
-- adjustments and transfers require a trusted backend service operation.
REVOKE ALL ON stock_skus, stock_warehouses, stock_balances, stock_movements
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON stock_skus, stock_warehouses, stock_balances, stock_movements
    TO authenticated;
GRANT SELECT, INSERT, UPDATE ON stock_skus, stock_warehouses TO service_role;
GRANT SELECT ON stock_balances, stock_movements TO service_role;
-- Ordinary service queries cannot bypass the ledger to directly post stock.

-- One DB transaction atomically creates the immutable event and updates the
-- balance. The unique event key is tenant-scoped and payload-checked on replay.
-- Concurrent updates serialize on the balance row; negative stock is rejected.
-- This RPC is intentionally NOT mounted to an HTTP endpoint yet.
CREATE OR REPLACE FUNCTION post_stock_movement(
    p_company_id UUID,
    p_sku TEXT,
    p_warehouse_id UUID,
    p_lot_code TEXT,
    p_bucket TEXT,
    p_unit TEXT,
    p_delta NUMERIC,
    p_source_type TEXT,
    p_source_ref TEXT,
    p_event_key TEXT,
    p_actor_user_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $
DECLARE
    v_id UUID;
    v_row stock_movements%ROWTYPE;
    v_qty NUMERIC(18,3);
    v_lot TEXT := btrim(coalesce(p_lot_code,''));
BEGIN
    IF p_company_id IS NULL OR p_actor_user_id IS NULL OR
       p_sku IS NULL OR btrim(p_sku) = '' OR
       p_warehouse_id IS NULL OR
       p_delta IS NULL OR p_delta = 0 OR
       p_delta::TEXT = 'NaN' OR
       p_delta <> round(p_delta, 3) OR
       p_bucket NOT IN ('available','damaged','quarantined') OR
       p_bucket IS NULL OR
       p_unit NOT IN ('case','unit','pallet') OR p_unit IS NULL OR
       p_source_type NOT IN (
         'opening_count','warehouse_receipt','shipment','transfer',
         'approved_adjustment','return','quarantine_release'
       ) OR p_source_type IS NULL OR
       p_source_ref IS NULL OR btrim(p_source_ref) = '' OR
       p_event_key IS NULL OR length(btrim(p_event_key)) NOT BETWEEN 1 AND 200
    THEN
        RAISE EXCEPTION 'invalid stock movement' USING ERRCODE='22023';
    END IF;

    -- Reject spoofed tenant/actor. Only an active authorized employee may
    -- be named by the trusted backend caller. This does not replace the
    -- server-side approval engine for consequential events.
    IF NOT EXISTS (
        SELECT 1 FROM users
        WHERE id = p_actor_user_id AND company_id = p_company_id
          AND is_active = TRUE AND role IN ('owner','manager','back_office')
    ) THEN
        RAISE EXCEPTION 'invalid or unauthorized stock movement actor'
          USING ERRCODE='42501';
    END IF;

    -- Enforce the SKU unit and require the warehouse belong to the tenant.
    IF NOT EXISTS (
        SELECT 1 FROM stock_skus
        WHERE company_id = p_company_id AND sku = p_sku AND base_unit = p_unit
    ) OR NOT EXISTS (
        SELECT 1 FROM stock_warehouses
        WHERE company_id = p_company_id AND id = p_warehouse_id
    ) THEN
        RAISE EXCEPTION 'invalid tenant SKU, unit or warehouse'
          USING ERRCODE='23503';
    END IF;

    INSERT INTO stock_movements(
      company_id, sku, warehouse_id, lot_code, bucket, unit,
      quantity_delta, source_type, source_ref, event_key, actor_user_id
    ) VALUES (
      p_company_id, p_sku, p_warehouse_id, v_lot, p_bucket, p_unit,
      p_delta, p_source_type, p_source_ref, p_event_key, p_actor_user_id
    ) ON CONFLICT (company_id, event_key) DO NOTHING
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        SELECT * INTO v_row FROM stock_movements
         WHERE company_id = p_company_id AND event_key = p_event_key;
        IF NOT FOUND OR v_row.sku <> p_sku OR
           v_row.warehouse_id <> p_warehouse_id OR
           v_row.lot_code <> v_lot OR v_row.bucket <> p_bucket OR
           v_row.unit <> p_unit OR v_row.quantity_delta <> p_delta OR
           v_row.source_type <> p_source_type OR
           v_row.source_ref <> p_source_ref OR
           v_row.actor_user_id <> p_actor_user_id
        THEN
            RAISE EXCEPTION 'idempotency key reused with different movement'
              USING ERRCODE='23505';
        END IF;
        RETURN jsonb_build_object('movement_id', v_row.id, 'replayed', TRUE);
    END IF;

    INSERT INTO stock_balances (
        company_id, sku, warehouse_id, lot_code, bucket, unit, quantity
    ) VALUES (
        p_company_id, p_sku, p_warehouse_id, v_lot, p_bucket, p_unit, 0
    ) ON CONFLICT DO NOTHING;

    UPDATE stock_balances
       SET quantity = quantity + p_delta, updated_at = now()
     WHERE company_id = p_company_id AND sku = p_sku
       AND warehouse_id = p_warehouse_id AND lot_code = v_lot
       AND bucket = p_bucket AND unit = p_unit
       AND quantity + p_delta >= 0
    RETURNING quantity INTO v_qty;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'insufficient balance; negative inventory prohibited'
          USING ERRCODE='23514';
    END IF;

    RETURN jsonb_build_object(
        'movement_id', v_id, 'replayed', FALSE, 'balance', v_qty
    );
END;
$$;

-- Direct clients must not invoke this procedure, even with a forged company ID.
REVOKE ALL ON FUNCTION post_stock_movement(
    UUID,TEXT,UUID,TEXT,TEXT,TEXT,NUMERIC,TEXT,TEXT,TEXT,UUID
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION post_stock_movement(
    UUID,TEXT,UUID,TEXT,TEXT,TEXT,NUMERIC,TEXT,TEXT,TEXT,UUID
) TO service_role;
