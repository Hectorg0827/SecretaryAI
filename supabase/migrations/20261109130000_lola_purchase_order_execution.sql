-- Lion BI / Lola: atomic approval + immutable payload for purchase orders.
-- Mirrors V14/V15 (approve_and_queue_email / claim_approved_email). This
-- migration queues nothing by itself and creates no worker or schedule; an
-- approved PO is only ever created in QuickBooks by the gated executor.

CREATE TABLE IF NOT EXISTS agent_execution_po_payloads (
    company_id     UUID NOT NULL,
    execution_id   UUID NOT NULL,
    vendor_id      TEXT NOT NULL CHECK (length(btrim(vendor_id)) BETWEEN 1 AND 64),
    lines          JSONB NOT NULL CHECK (jsonb_typeof(lines) = 'array'
                                         AND jsonb_array_length(lines) BETWEEN 1 AND 100),
    total          NUMERIC(14,2) NOT NULL CHECK (total >= 0 AND total <= 10000000),
    memo           TEXT NOT NULL DEFAULT '' CHECK (length(memo) <= 1000),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, execution_id),
    FOREIGN KEY (company_id, execution_id) REFERENCES agent_executions(company_id, id)
);
ALTER TABLE agent_execution_po_payloads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON agent_execution_po_payloads
    FROM PUBLIC, anon, authenticated, service_role, secretary_executor;

CREATE OR REPLACE FUNCTION reject_po_payload_rewrite() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $immutable$
BEGIN
    RAISE EXCEPTION 'approved purchase order payload is immutable' USING ERRCODE = '42501';
END
$immutable$;
DROP TRIGGER IF EXISTS trg_approved_po_payload_immutable ON agent_execution_po_payloads;
CREATE TRIGGER trg_approved_po_payload_immutable
    BEFORE UPDATE OR DELETE ON agent_execution_po_payloads
    FOR EACH ROW EXECUTE FUNCTION reject_po_payload_rewrite();

CREATE OR REPLACE FUNCTION secretary_internal.approve_and_queue_purchase_order(
    p_company_id UUID, p_draft_id UUID, p_actor UUID,
    p_expected_content JSONB, p_idempotency_key TEXT,
    p_edited_content JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $approve$
DECLARE
    d drafts%ROWTYPE; v_execution_id UUID; next_status TEXT;
    payload JSONB; line JSONB; v_vendor TEXT; v_lines JSONB := '[]'::JSONB;
    v_qty NUMERIC; v_cost NUMERIC; v_total NUMERIC := 0; v_item TEXT;
BEGIN
    IF p_company_id IS NULL OR p_draft_id IS NULL OR p_actor IS NULL OR
       p_expected_content IS NULL OR jsonb_typeof(p_expected_content) <> 'object' OR
       p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 200 OR
       (p_edited_content IS NOT NULL AND jsonb_typeof(p_edited_content) <> 'object') THEN
        RAISE EXCEPTION 'invalid approval request' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM users WHERE id = p_actor AND company_id = p_company_id
          AND is_active AND role IN ('owner', 'manager')
    ) THEN
        RAISE EXCEPTION 'approval actor is not authorized' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO d FROM drafts WHERE company_id = p_company_id AND id = p_draft_id FOR UPDATE;
    IF NOT FOUND OR d.action_type <> 'purchase_order' THEN
        RAISE EXCEPTION 'eligible purchase order draft not found' USING ERRCODE = 'P0002';
    END IF;
    IF d.content IS DISTINCT FROM p_expected_content THEN
        RAISE EXCEPTION 'draft changed since policy evaluation' USING ERRCODE = '40001';
    END IF;
    IF d.status IN ('approved', 'edited_and_approved') THEN
        SELECT id INTO v_execution_id FROM agent_executions
         WHERE company_id = p_company_id AND draft_id = p_draft_id
           AND idempotency_key = p_idempotency_key;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM agent_execution_po_payloads e
             WHERE e.company_id = p_company_id AND e.execution_id = v_execution_id
        ) THEN
            RAISE EXCEPTION 'approved draft lacks matching immutable payload' USING ERRCODE = '23505';
        END IF;
        RETURN jsonb_build_object('queued', FALSE, 'already_processed', TRUE,
                                  'execution_id', v_execution_id, 'status', d.status);
    END IF;
    IF d.status <> 'pending' THEN
        RAISE EXCEPTION 'draft is not pending' USING ERRCODE = '42501';
    END IF;

    payload := coalesce(p_edited_content, d.content);
    v_vendor := btrim(coalesce(payload->>'vendor_id', ''));
    IF length(v_vendor) NOT BETWEEN 1 AND 64 THEN
        RAISE EXCEPTION 'purchase order vendor is required' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(payload->'lines') IS DISTINCT FROM 'array'
       OR jsonb_array_length(payload->'lines') NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'purchase order needs 1 to 100 lines' USING ERRCODE = '22023';
    END IF;
    FOR line IN SELECT * FROM jsonb_array_elements(payload->'lines') LOOP
        v_item := btrim(coalesce(line->>'item_id', ''));
        BEGIN
            v_qty := (line->>'quantity')::NUMERIC;
            v_cost := (line->>'unit_cost')::NUMERIC;
        EXCEPTION WHEN others THEN
            RAISE EXCEPTION 'purchase order line is not numeric' USING ERRCODE = '22023';
        END;
        IF length(v_item) NOT BETWEEN 1 AND 64 OR v_qty IS NULL OR v_qty <= 0 OR v_qty > 1000000
           OR v_cost IS NULL OR v_cost < 0 OR v_cost > 10000000 THEN
            RAISE EXCEPTION 'invalid purchase order line' USING ERRCODE = '22023';
        END IF;
        v_total := v_total + v_qty * v_cost;
        v_lines := v_lines || jsonb_build_array(jsonb_build_object(
            'item_id', v_item, 'quantity', v_qty, 'unit_cost', v_cost,
            'description', left(coalesce(line->>'description', ''), 200)));
    END LOOP;
    v_total := round(v_total, 2);
    -- The approved amount (what policy and limits were evaluated against)
    -- must match the lines exactly.
    IF payload ? 'total' AND abs((payload->>'total')::NUMERIC - v_total) > 0.01 THEN
        RAISE EXCEPTION 'purchase order total does not match its lines' USING ERRCODE = '22023';
    END IF;
    IF v_total > 10000000 THEN
        RAISE EXCEPTION 'purchase order total exceeds the automated limit' USING ERRCODE = '22023';
    END IF;

    next_status := CASE WHEN p_edited_content IS NULL THEN 'approved' ELSE 'edited_and_approved' END;
    UPDATE drafts SET status = next_status, reviewed_by = p_actor, reviewed_at = now(), content = payload
     WHERE company_id = p_company_id AND id = p_draft_id;
    INSERT INTO agent_executions(company_id, draft_id, idempotency_key, action_type)
        VALUES (p_company_id, p_draft_id, p_idempotency_key, 'purchase_order')
        RETURNING id INTO v_execution_id;
    INSERT INTO agent_execution_po_payloads(company_id, execution_id, vendor_id, lines, total, memo,
                                            payload_sha256)
    VALUES (p_company_id, v_execution_id, v_vendor, v_lines, v_total,
            left('Lion BI approved purchase order ' || p_draft_id::TEXT, 1000),
            encode(digest(convert_to(payload::TEXT, 'UTF8'), 'sha256'), 'hex'));
    RETURN jsonb_build_object('queued', TRUE, 'already_processed', FALSE,
                              'execution_id', v_execution_id, 'status', next_status);
END
$approve$;

CREATE OR REPLACE FUNCTION secretary_internal.claim_approved_purchase_order(
    p_company_id UUID, p_execution_id UUID, p_worker_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $claim$
DECLARE p agent_execution_po_payloads%ROWTYPE; result JSONB;
BEGIN
    SELECT * INTO p FROM agent_execution_po_payloads
     WHERE company_id = p_company_id AND execution_id = p_execution_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'immutable purchase order payload unavailable' USING ERRCODE = 'P0002';
    END IF;
    result := secretary_internal.claim_agent_execution(p_company_id, p_execution_id, p_worker_id);
    IF result->>'claimed' <> 'true' THEN RETURN result; END IF;
    RETURN result || jsonb_build_object(
        'vendor_id', p.vendor_id, 'lines', p.lines, 'total', p.total,
        'memo', p.memo, 'payload_sha256', p.payload_sha256);
END
$claim$;

REVOKE ALL ON FUNCTION
    secretary_internal.approve_and_queue_purchase_order(UUID, UUID, UUID, JSONB, TEXT, JSONB),
    secretary_internal.claim_approved_purchase_order(UUID, UUID, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION
    secretary_internal.approve_and_queue_purchase_order(UUID, UUID, UUID, JSONB, TEXT, JSONB),
    secretary_internal.claim_approved_purchase_order(UUID, UUID, TEXT)
    TO secretary_executor;
