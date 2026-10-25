-- Phase 4: AccessRouter hardening
-- ---------------------------------------------------------------------------
-- Add capability_routes JSONB to company_features so each tenant can
-- override which path chains are tried per capability.
--
-- Format: {"inventory": ["api", "file_ingestion"], "customers": ["api"]}
-- Keys are capability names; values are ordered lists of path names.
-- If a capability is absent, the built-in default chain is used.
-- ---------------------------------------------------------------------------

ALTER TABLE company_features
    ADD COLUMN IF NOT EXISTS capability_routes jsonb DEFAULT '{}';

COMMENT ON COLUMN company_features.capability_routes IS
    'Per-tenant capability → path chain overrides for AccessRouter. '
    'Keys: capability names. Values: ordered list of path names '
    '(api, playwright, file_ingestion, computer_use). '
    'Absent capabilities fall back to the built-in defaults.';

-- Also add correlation_id to browser_jobs for end-to-end tracing
ALTER TABLE browser_jobs
    ADD COLUMN IF NOT EXISTS correlation_id text;

CREATE INDEX IF NOT EXISTS idx_browser_jobs_correlation
    ON browser_jobs (correlation_id)
    WHERE correlation_id IS NOT NULL;
