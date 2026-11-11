-- Compliance Engine DB Migration
-- Creates tables for tracking alcohol beverage compliance entities.

-- Products tracked for compliance
CREATE TABLE IF NOT EXISTS compliance_products (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sku text NOT NULL,
    name text NOT NULL,
    product_type text NOT NULL,  -- 'wine'|'spirits'|'beer'|'malt'
    abv_pct numeric(5,2) DEFAULT 0,
    container_size_ml numeric(8,2) DEFAULT 750,
    cases_per_container int DEFAULT 56,
    unit_cost_fob numeric(10,2) DEFAULT 0,
    country_of_origin text DEFAULT '',
    foreign_producer_id text DEFAULT '',
    requires_formula_approval boolean DEFAULT false,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- Federal permits (TTB, FDA, CBP)
CREATE TABLE IF NOT EXISTS compliance_federal_permits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    permit_type text NOT NULL,
    permit_number text NOT NULL,
    issue_date date,
    expiration_date date,
    status text DEFAULT 'active',
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- TTB COLA records
CREATE TABLE IF NOT EXISTS compliance_colas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id uuid NOT NULL,
    cola_number text NOT NULL,
    product_type text NOT NULL,
    issue_date date,
    expiration_date date,
    status text DEFAULT 'active',
    formula_approved boolean DEFAULT false,
    lab_analysis_on_file boolean DEFAULT false,
    created_at timestamptz DEFAULT now()
);

-- State licenses
CREATE TABLE IF NOT EXISTS compliance_licenses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    state_code text NOT NULL,
    license_type text NOT NULL,
    product_types text[] DEFAULT '{}',
    license_number text NOT NULL,
    issue_date date,
    expiration_date date,
    renewal_window_days int DEFAULT 90,
    annual_fee numeric(10,2) DEFAULT 0,
    status text DEFAULT 'active',
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Brand registrations (per product per state)
CREATE TABLE IF NOT EXISTS compliance_brand_registrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id uuid NOT NULL,
    state_code text NOT NULL,
    registration_number text DEFAULT '',
    registration_date date,
    expiration_date date,
    registration_fee numeric(10,2) DEFAULT 0,
    status text DEFAULT 'active',
    state_label_approval_number text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Distributor relationships
CREATE TABLE IF NOT EXISTS compliance_distributors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    state_code text NOT NULL,
    distributor_name text NOT NULL,
    territory text DEFAULT 'Statewide',
    product_types text[] DEFAULT '{}',
    contract_start_date date,
    contract_end_date date,
    franchise_law_attached boolean DEFAULT false,
    franchise_attachment_date date,
    termination_restriction text DEFAULT 'none',
    contract_document_ref text DEFAULT '',
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Compliance check audit trail
CREATE TABLE IF NOT EXISTS compliance_check_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    product_id uuid,
    state_code text,
    quantity_cases int DEFAULT 1,
    approved boolean NOT NULL,
    issues_count int DEFAULT 0,
    blockers_count int DEFAULT 0,
    total_fees numeric(10,2) DEFAULT 0,
    checked_at timestamptz DEFAULT now()
);

-- Add compliance_active_states to companies if not present
ALTER TABLE companies ADD COLUMN IF NOT EXISTS compliance_active_states text DEFAULT '';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_compliance_products_company ON compliance_products(company_id);
CREATE INDEX IF NOT EXISTS idx_compliance_licenses_company_state ON compliance_licenses(company_id, state_code);
CREATE INDEX IF NOT EXISTS idx_compliance_brand_reg_company ON compliance_brand_registrations(company_id, state_code);
CREATE INDEX IF NOT EXISTS idx_compliance_colas_company ON compliance_colas(company_id, product_id);
