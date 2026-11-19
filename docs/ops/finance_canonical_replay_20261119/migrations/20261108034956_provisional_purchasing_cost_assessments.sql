-- Staging-first additive purchasing finance ledger. NOT a QB posting migration.
-- A cost assessment is an immutable assertion until separately verified against
-- real supplier evidence. Approved physical receipts stay untouched.
CREATE TABLE IF NOT EXISTS purchasing_cost_assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    po_id UUID NOT NULL,
    assessment_key TEXT NOT NULL CHECK (length(btrim(assessment_key)) BETWEEN 1 AND 150),
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    fx_rate_to_usd NUMERIC(18,6) NOT NULL CHECK (
        fx_rate_to_usd > 0 AND fx_rate_to_usd <= 1000000000000
        AND (currency <> 'USD' OR fx_rate_to_usd = 1)
    ),
    landed_cost_total NUMERIC(18,6) NOT NULL CHECK (
        landed_cost_total >= 0 AND landed_cost_total <= 999999999999.999999
    ),
    lines JSONB NOT NULL CHECK (jsonb_typeof(lines) = 'array'),
    assertion_status TEXT NOT NULL DEFAULT 'provisional'
        CHECK (assertion_status = 'provisional'),
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    UNIQUE(company_id,po_id,assessment_key),
    FOREIGN KEY(company_id,po_id) REFERENCES purchasing_orders(company_id,id)
);
CREATE INDEX IF NOT EXISTS idx_cost_assessments_po
    ON purchasing_cost_assessments(company_id,po_id,created_at DESC);

ALTER TABLE purchasing_cost_assessments ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_read_purchasing_cost_assessments
    ON purchasing_cost_assessments FOR SELECT
    USING(company_id = (auth.jwt()->>'company_id')::UUID);
REVOKE ALL ON purchasing_cost_assessments FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON purchasing_cost_assessments TO authenticated,service_role;

CREATE OR REPLACE FUNCTION forbid_purchasing_cost_assessment_rewrite()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,public,pg_temp AS $immut$
BEGIN
    RAISE EXCEPTION 'Cost assessments are immutable; create a new keyed assessment'
        USING ERRCODE = '23514';
END
$immut$;
CREATE TRIGGER immutable_purchasing_cost_assessment
    BEFORE UPDATE OR DELETE ON purchasing_cost_assessments
    FOR EACH ROW EXECUTE FUNCTION forbid_purchasing_cost_assessment_rewrite();

CREATE OR REPLACE FUNCTION record_provisional_purchasing_cost_assessment(
    p_company_id UUID, p_po_number TEXT, p_assessment_key TEXT,
    p_currency TEXT, p_fx_rate_to_usd NUMERIC,
    p_landed_cost_total NUMERIC, p_lines JSONB, p_actor UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $finance$
DECLARE
    v_po purchasing_orders%ROWTYPE;
    v_item JSONB;
    v_sku TEXT;
    v_qty NUMERIC;
    v_cost NUMERIC;
    v_ordered NUMERIC;
    v_seen TEXT[] := ARRAY[]::TEXT[];
    v_id UUID;
BEGIN
    IF p_company_id IS NULL OR p_actor IS NULL OR
       p_po_number IS NULL OR length(btrim(p_po_number)) NOT BETWEEN 1 AND 100 OR
       p_assessment_key IS NULL OR length(btrim(p_assessment_key)) NOT BETWEEN 1 AND 150 OR
       p_currency IS NULL OR p_currency !~ '^[A-Z]{3}$' OR
       p_fx_rate_to_usd IS NULL OR p_fx_rate_to_usd <= 0 OR
       p_fx_rate_to_usd > 999999999999.999999 OR p_fx_rate_to_usd = 'NaN'::NUMERIC OR
       (p_currency = 'USD' AND p_fx_rate_to_usd <> 1) OR
       p_fx_rate_to_usd <> round(p_fx_rate_to_usd,6) OR
       p_landed_cost_total IS NULL OR p_landed_cost_total < 0 OR
       p_landed_cost_total > 999999999999.999999 OR
       p_landed_cost_total = 'NaN'::NUMERIC OR
       p_landed_cost_total <> round(p_landed_cost_total,6) OR
       p_lines IS NULL OR jsonb_typeof(p_lines) <> 'array' OR
       jsonb_array_length(p_lines) NOT BETWEEN 1 AND 100
    THEN
        RAISE EXCEPTION 'Invalid provisional purchasing cost assessment' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM users WHERE id=p_actor AND company_id=p_company_id
        AND is_active AND role IN ('owner','manager','back_office')) THEN
        RAISE EXCEPTION 'Unauthorized financial assessment' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_po FROM purchasing_orders
        WHERE company_id=p_company_id AND po_number=p_po_number;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Purchase order not found' USING ERRCODE='P0002';
    END IF;
    IF v_po.status = 'draft' THEN
        RAISE EXCEPTION 'PO must be approved before costing' USING ERRCODE='23514';
    END IF;
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_lines) LOOP
        IF jsonb_typeof(v_item) <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 3 OR
           NOT (v_item ? 'sku' AND v_item ? 'qty_ordered' AND v_item ? 'unit_cost') THEN
            RAISE EXCEPTION 'Invalid financial line keys' USING ERRCODE='22023';
        END IF;
        v_sku := NULLIF(btrim(v_item->>'sku'),'');
        IF v_sku IS NULL OR v_sku = ANY(v_seen) OR
           (v_item->>'qty_ordered') !~ '^\d+(\.\d{1,3})?$' OR
           (v_item->>'unit_cost') !~ '^\d+(\.\d{1,6})?$' THEN
            RAISE EXCEPTION 'Invalid or duplicate financial line' USING ERRCODE='22023';
        END IF;
        v_qty := (v_item->>'qty_ordered')::NUMERIC;
        v_cost := (v_item->>'unit_cost')::NUMERIC;
        SELECT qty_ordered INTO v_ordered FROM purchasing_order_lines
           WHERE company_id=p_company_id AND po_id=v_po.id AND sku=v_sku;
        IF NOT FOUND OR v_qty <> v_ordered OR v_cost < 0 OR
           v_cost > 999999999999.999999 THEN
            RAISE EXCEPTION 'Financial line does not match approved PO' USING ERRCODE='23514';
        END IF;
        v_seen := array_append(v_seen,v_sku);
    END LOOP;
    IF cardinality(v_seen) <> (SELECT count(*) FROM purchasing_order_lines
        WHERE company_id=p_company_id AND po_id=v_po.id) THEN
        RAISE EXCEPTION 'Financial assessment is missing ordered SKUs' USING ERRCODE='23514';
    END IF;
    INSERT INTO purchasing_cost_assessments(
        company_id,po_id,assessment_key,currency,fx_rate_to_usd,
        landed_cost_total,lines,created_by
    ) VALUES (
        p_company_id,v_po.id,btrim(p_assessment_key),p_currency,
        p_fx_rate_to_usd,p_landed_cost_total,p_lines,p_actor
    ) RETURNING id INTO v_id;
    RETURN jsonb_build_object('assessment_id',v_id,'po_number',v_po.po_number,
      'status','provisional','valuation_verified',FALSE,
      'accounting_reconciled',FALSE,'quickbooks_posted',FALSE);
END
$finance$;

REVOKE ALL ON FUNCTION record_provisional_purchasing_cost_assessment(
    UUID,TEXT,TEXT,TEXT,NUMERIC,NUMERIC,JSONB,UUID)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION record_provisional_purchasing_cost_assessment(
    UUID,TEXT,TEXT,TEXT,NUMERIC,NUMERIC,JSONB,UUID)
    TO service_role;
