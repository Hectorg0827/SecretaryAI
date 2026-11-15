-- Phase 1 price posting: versioned candidate rule catalog, human review by tenant.
-- This migration seeds DOCUMENTARY STARTING POINTS only, not verified filing rules.
-- No deadline or regulatory approval is assumed. No agency submission is enabled.

CREATE TABLE IF NOT EXISTS public.compliance_price_posting_rule_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_key text NOT NULL CHECK (length(btrim(program_key)) BETWEEN 3 AND 80),
    version integer NOT NULL CHECK (version > 0),
    country_code text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    region_code text NOT NULL CHECK (region_code ~ '^[A-Z0-9-]{2,12}$'),
    program_name text NOT NULL CHECK (length(btrim(program_name)) > 3),
    required_details text[] NOT NULL DEFAULT '{}',
    allowed_channels text[] NOT NULL DEFAULT '{}',
    product_types text[] NOT NULL DEFAULT '{}',
    authority_url text NOT NULL CHECK (authority_url ~ '^https://'),
    authority_title text NOT NULL,
    official_template_url text,
    applicability_note text NOT NULL,
    filing_deadline_rule jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (program_key,version),
    CHECK (official_template_url IS NULL OR official_template_url ~ '^https://'),
    CHECK (filing_deadline_rule IS NULL OR jsonb_typeof(filing_deadline_rule) = 'object')
);

-- Retain both the rule version and its human attestation for an audit trail.
CREATE TABLE IF NOT EXISTS public.compliance_price_posting_rule_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE RESTRICT,
    rule_id uuid NOT NULL REFERENCES public.compliance_price_posting_rule_versions(id) ON DELETE RESTRICT,
    reviewer_id uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    evidence_ref text NOT NULL CHECK (length(btrim(evidence_ref)) >= 10),
    review_note text NOT NULL CHECK (length(btrim(review_note)) >= 20),
    UNIQUE (company_id,rule_id)
);
CREATE INDEX IF NOT EXISTS idx_price_rule_reviews_company
    ON public.compliance_price_posting_rule_reviews(company_id,rule_id);

-- Reviews must be performed by an active human owner/manager OF THAT TENANT.
-- Do not let a service-role caller supply a reviewer from another company.
CREATE FUNCTION public.price_posting_rule_review_identity_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.users u
         WHERE u.id = NEW.reviewer_id
           AND u.company_id = NEW.company_id
           AND u.is_active IS TRUE
           AND u.role IN ('owner','manager')
    ) THEN
        RAISE EXCEPTION 'Price rule human reviewer is not authorized for this tenant'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$guard$;

CREATE TRIGGER trg_price_rule_review_identity
BEFORE INSERT ON public.compliance_price_posting_rule_reviews
FOR EACH ROW EXECUTE FUNCTION public.price_posting_rule_review_identity_guard();

-- No rewriting reviewed evidence or rule versions; publish a NEW version
-- when official guidance changes, then require tenant re-attestation.
CREATE FUNCTION public.reject_price_posting_rule_history_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $immutable$
BEGIN
    RAISE EXCEPTION 'Verified regulatory history is immutable; create a new version'
        USING ERRCODE = '42501';
END
$immutable$;

CREATE TRIGGER trg_price_rule_versions_immutable
BEFORE UPDATE OR DELETE ON public.compliance_price_posting_rule_versions
FOR EACH ROW EXECUTE FUNCTION public.reject_price_posting_rule_history_mutation();
CREATE TRIGGER trg_price_rule_reviews_immutable
BEFORE UPDATE OR DELETE ON public.compliance_price_posting_rule_reviews
FOR EACH ROW EXECUTE FUNCTION public.reject_price_posting_rule_history_mutation();


ALTER TABLE public.compliance_price_posting_rule_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.compliance_price_posting_rule_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.compliance_price_posting_rule_versions,
    public.compliance_price_posting_rule_reviews FROM PUBLIC,anon,authenticated,service_role;
-- The backend may READ. A separate audited human-verification gateway is
-- required before any tenant can create review records.
GRANT SELECT ON public.compliance_price_posting_rule_versions,
    public.compliance_price_posting_rule_reviews TO service_role;

INSERT INTO public.compliance_price_posting_rule_versions
    (program_key,version,country_code,region_code,program_name,
     required_details,allowed_channels,product_types,authority_url,
     authority_title,applicability_note,filing_deadline_rule)
VALUES
('NY_WHOLESALE',1,'US','NY','NY wholesale wine and liquor price posting',
 ARRAY['brand_name','product_type','units_per_case','supplier_license_number'],
 ARRAY['distributor'],ARRAY['wine','spirits'],
 'https://sla.ny.gov/node/1141',
 'New York State Liquor Authority: Price Posting',
 'Candidate scope only; confirm filer category, exceptions, discounts, source license, month and deadline with NY SLA.',NULL),
('NY_RETAIL',1,'US','NY','NY retail wine and liquor price posting',
 ARRAY['brand_name','product_type','units_per_case','supplier_license_number'],
 ARRAY['retail','on_premise'],ARRAY['wine','spirits'],
 'https://sla.ny.gov/node/1141',
 'New York State Liquor Authority: Price Posting',
 'Candidate scope only; confirm filer category, actual retailer channel, discounts, source license, month and deadline.',NULL),
('NJ_ECPL',1,'US','NJ','New Jersey Current Price List',
 ARRAY['brand_name','product_type','units_per_case','upc',
       'brand_registration_number','license_number','terms_and_conditions'],
 ARRAY['retail','on_premise'],ARRAY['wine','spirits','beer','malt'],
 'https://www.nj.gov/oag/abc/downloads/AN%202025-03-eCPL-Advisory-Notice.pdf',
 'New Jersey ABC: prescribed eCPL template, AN 2025-03',
 'Candidate scope only; the live POSSE template, current format, promotional terms and deadlines require human confirmation.',NULL),
('FL_MALT_NOTICE',1,'US','FL','Florida malt beverage price differential/change notice',
 ARRAY['brand_name','product_type','units_per_case','posting_type',
       'cash_price_after_posting','counties','license_number'],
 ARRAY['retail','on_premise'],ARRAY['beer','malt'],
 'https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/forms-and-publications/',
 'Florida DBPR: Notice of Differential Prices or Change of Prices (Malt Beverages)',
 'Candidate scope only; verify licensee applicability, EDS fields, county selection and notice timing.',NULL)
ON CONFLICT (program_key,version) DO NOTHING;

COMMENT ON TABLE public.compliance_price_posting_rule_versions IS
 'Unverified regulatory source versions; no rule is effective until tenant human review with evidence.';
COMMENT ON TABLE public.compliance_price_posting_rule_reviews IS
 'Tenant-specific human verification evidence; service-role direct writes intentionally prohibited pending audited gateway.';
