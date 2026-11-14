-- Phase 1 pricing foundation. Additive, staging-first: NO price/accounting writes.
-- Cost snapshots and price-list history have tenant-scoped identities and evidence.
-- Deploy only after a separate migration/RLS test and recorded operator approval.
-- The application uses server-side authorization and a service-role READ ONLY grant.
-- Price approval/creation and external sync are separate future gated workflows.

CREATE TABLE IF NOT EXISTS pricing_cost_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sku TEXT NOT NULL CHECK (length(btrim(sku)) BETWEEN 1 AND 120),
    shipment_ref TEXT NOT NULL CHECK (length(btrim(shipment_ref)) BETWEEN 1 AND 120),
    warehouse_ref TEXT NOT NULL DEFAULT '' CHECK (length(warehouse_ref) <= 120),
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
    accepted_cases NUMERIC(18,3) NOT NULL CHECK (accepted_cases > 0),
    cost_as_of DATE NOT NULL,
    expected_storage_days INTEGER NOT NULL CHECK (expected_storage_days >= 0 AND expected_storage_days <= 3660),
    source_revision TEXT NOT NULL CHECK (length(btrim(source_revision)) BETWEEN 1 AND 120),
    evidence_status TEXT NOT NULL DEFAULT 'provisional'
        CHECK (evidence_status IN ('provisional','reconciled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    UNIQUE(company_id,sku,shipment_ref,warehouse_ref,source_revision)
);

CREATE TABLE IF NOT EXISTS pricing_cost_lines (
    company_id UUID NOT NULL,
    cost_snapshot_id UUID NOT NULL,
    expense_line_ref TEXT NOT NULL CHECK (length(btrim(expense_line_ref)) BETWEEN 1 AND 160),
    component TEXT NOT NULL CHECK (component IN (
        'product','international_freight','insurance','duty','customs_brokerage',
        'port_fees','domestic_freight','excise','other_taxes',
        'fuel_surcharge','origin_charges','demurrage','exam_fees',
        'warehouse_receiving','warehouse_storage','warehouse_handling',
        'warehouse_outbound','warehouse_shrink','inventory_carrying'
    )),
    cost_per_case NUMERIC(18,6) NOT NULL CHECK (cost_per_case >= 0 AND cost_per_case <= 999999999999.999999),
    source_document_ref TEXT NOT NULL CHECK (length(btrim(source_document_ref)) BETWEEN 1 AND 160),
    source_revision TEXT NOT NULL CHECK (length(btrim(source_revision)) BETWEEN 1 AND 120),
    allocation_basis TEXT NOT NULL CHECK (allocation_basis IN (
        'direct_per_case','cases','weight_kg','volume_cbm','pallet_days','picks','manual_estimate'
    )),
    observation_status TEXT NOT NULL DEFAULT 'provisional'
        CHECK (observation_status IN ('provisional','verified')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id,cost_snapshot_id,expense_line_ref),
    FOREIGN KEY(company_id,cost_snapshot_id)
        REFERENCES pricing_cost_snapshots(company_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS pricing_price_book (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sku TEXT NOT NULL CHECK (length(btrim(sku)) BETWEEN 1 AND 120),
    package_label TEXT NOT NULL CHECK (length(btrim(package_label)) BETWEEN 1 AND 80),
    market_code TEXT NOT NULL CHECK (length(btrim(market_code)) BETWEEN 1 AND 60),
    channel TEXT NOT NULL CHECK (channel IN ('distributor','retail','on_premise','direct','other')),
    customer_ref TEXT NOT NULL DEFAULT '' CHECK (length(customer_ref) <= 120),
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
    list_price_per_case NUMERIC(18,6) NOT NULL CHECK (list_price_per_case > 0),
    effective_from DATE NOT NULL,
    effective_to DATE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','pending_approval','approved','retired')),
    cost_snapshot_id UUID,
    evidence_ref TEXT NOT NULL DEFAULT '',
    created_by UUID REFERENCES users(id),
    approved_by UUID REFERENCES users(id),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    UNIQUE(company_id,sku,package_label,market_code,channel,customer_ref,effective_from,revision),
    FOREIGN KEY(company_id,cost_snapshot_id)
        REFERENCES pricing_cost_snapshots(company_id,id) ON DELETE RESTRICT,
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CHECK (
        status <> 'approved' OR
        (cost_snapshot_id IS NOT NULL AND approved_by IS NOT NULL
         AND approved_at IS NOT NULL AND created_by IS NOT NULL
         AND created_by <> approved_by AND length(btrim(evidence_ref)) > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_pricing_cost_sku
    ON pricing_cost_snapshots(company_id,sku,cost_as_of DESC);
CREATE INDEX IF NOT EXISTS idx_pricing_book_lookup
    ON pricing_price_book(company_id,sku,market_code,channel,effective_from DESC);
CREATE INDEX IF NOT EXISTS idx_pricing_book_status
    ON pricing_price_book(company_id,status,effective_from DESC);

ALTER TABLE pricing_cost_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE pricing_cost_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE pricing_price_book ENABLE ROW LEVEL SECURITY;

-- Deliberately no public/authenticated policies: pricing and supplier costs
-- are confidential. All reads must go through the server's current-role check
-- and explicitly filter by company_id. No UI or browser DB credentials.
REVOKE ALL ON pricing_cost_snapshots,pricing_cost_lines,pricing_price_book
    FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON pricing_cost_snapshots,pricing_cost_lines,pricing_price_book
    TO service_role;
