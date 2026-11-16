-- Phase 1 Patch 10: read-only QB source item identity / pack-candidate register.
-- NO QB connection, credential access, observation ingestion, approved mapping,
-- regulatory export, accounting update, or price publication is authorized.
-- The service role may SELECT, never INSERT or UPDATE. CI fixtures run as owner.

CREATE TABLE public.pricing_accounting_capture_bindings(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE RESTRICT,
    price_book_id uuid NOT NULL,
    provider text NOT NULL CHECK(provider IN ('qbo','qbd')),
    source_item_id text NOT NULL CHECK(length(btrim(source_item_id)) BETWEEN 1 AND 160),
    source_revision text NOT NULL CHECK(length(btrim(source_revision)) BETWEEN 1 AND 160),
    source_sku_hint text CHECK(source_sku_hint IS NULL OR length(source_sku_hint) <= 120),
    claimed_currency text NOT NULL CHECK(claimed_currency ~ '^[A-Z]{3}$'),
    candidate_unit_basis text NOT NULL
        CHECK(candidate_unit_basis IN ('case','single_unit','unknown')),
    candidate_units_per_case integer CHECK(
        candidate_units_per_case IS NULL OR candidate_units_per_case BETWEEN 1 AND 1000
    ),
    candidate_document_ref text NOT NULL
        CHECK(length(btrim(candidate_document_ref)) BETWEEN 10 AND 1500),
    identity_state text NOT NULL DEFAULT 'candidate_unverified'
        CHECK(identity_state='candidate_unverified'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(company_id,price_book_id)
        REFERENCES public.pricing_price_book(company_id,id) ON DELETE RESTRICT,
    UNIQUE(company_id,price_book_id,provider,source_item_id,source_revision),
    CHECK (
        (candidate_unit_basis = 'single_unit' AND candidate_units_per_case IS NOT NULL)
        OR (candidate_unit_basis IN ('case','unknown')
            AND candidate_units_per_case IS NULL)
    )
);
CREATE INDEX idx_capture_bindings_tenant_book_provider
  ON public.pricing_accounting_capture_bindings(company_id,price_book_id,provider,created_at DESC);

CREATE TRIGGER trg_capture_bindings_immutable
BEFORE UPDATE OR DELETE ON public.pricing_accounting_capture_bindings
FOR EACH ROW EXECUTE FUNCTION public.reject_price_preparation_history_rewrite();

ALTER TABLE public.pricing_accounting_capture_bindings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.pricing_accounting_capture_bindings
    FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.pricing_accounting_capture_bindings TO service_role;

COMMENT ON TABLE public.pricing_accounting_capture_bindings IS
'Immutable UNVERIFIED QBO/QBD item-ID and case-pack candidates. No capture or client-write API; no automatic SKU identity or financial reconciliation.';
