-- Patch 9: READ-ONLY accounting observation contract for synthetic pilot.
-- The source row is NOT a verified QBO/QBD posting, current item price or
-- regulatory filing. This migration creates NO ingestion/write authority.
-- A future authorized, independently tested adapter capture must populate it.

CREATE TABLE public.pricing_accounting_price_observations(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE RESTRICT,
    price_book_id uuid NOT NULL,
    provider text NOT NULL CHECK(provider IN ('qbo','qbd')),
    source_item_id text NOT NULL CHECK(length(btrim(source_item_id)) BETWEEN 1 AND 160),
    source_revision text NOT NULL CHECK(length(btrim(source_revision)) BETWEEN 1 AND 160),
    sku text NOT NULL CHECK(length(btrim(sku)) BETWEEN 1 AND 120),
    currency text NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
    observed_unit_price numeric(18,6) NOT NULL CHECK(
        observed_unit_price >= 0 AND observed_unit_price <= 999999999999.999999
    ),
    unit_basis text NOT NULL CHECK(unit_basis IN ('case','single_unit','unknown')),
    units_per_case integer CHECK(
        units_per_case IS NULL OR units_per_case BETWEEN 1 AND 1000
    ),
    source_document_ref text NOT NULL CHECK(
        length(btrim(source_document_ref)) BETWEEN 10 AND 1500
    ),
    observed_at timestamptz NOT NULL,
    source_origin text NOT NULL DEFAULT 'synthetic_test'
        CHECK(source_origin IN ('synthetic_test','connector_snapshot_unverified')),
    observation_state text NOT NULL DEFAULT 'internally_recorded_unverified'
        CHECK(observation_state = 'internally_recorded_unverified'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(company_id,id),
    FOREIGN KEY(company_id,price_book_id)
        REFERENCES public.pricing_price_book(company_id,id) ON DELETE RESTRICT,
    CHECK (
        (unit_basis = 'single_unit' AND units_per_case IS NOT NULL)
        OR (unit_basis IN ('case','unknown') AND units_per_case IS NULL)
    )
);
CREATE INDEX idx_accounting_observations_tenant_book
    ON public.pricing_accounting_price_observations(company_id,price_book_id,created_at DESC);

-- An observation is an immutable historical statement. A later observation
-- must be appended; correcting one silently would destroy provenance.
CREATE TRIGGER trg_accounting_observations_immutable
BEFORE UPDATE OR DELETE ON public.pricing_accounting_price_observations
FOR EACH ROW EXECUTE FUNCTION public.reject_price_preparation_history_rewrite();

ALTER TABLE public.pricing_accounting_price_observations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.pricing_accounting_price_observations
    FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.pricing_accounting_price_observations TO service_role;

COMMENT ON TABLE public.pricing_accounting_price_observations IS
'Append-only unverified accounting price observations. No client insert, no connector call, no filing certification. Synthetic fixtures until separately authorized adapter capture exists.';
