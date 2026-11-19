-- Forward-only schema contract for QuickBooks OAuth token rotation.
-- Staging has ALREADY received the equivalent idempotent change through
-- staging_supabase/patches/20261106030845_qbo_token_refresh_timestamp.sql.
-- Recording this migration does not execute it in production; production
-- rollout requires a separate reviewed migration/deployment authorization.
ALTER TABLE public.companies
    ADD COLUMN IF NOT EXISTS qbo_token_refreshed_at TIMESTAMPTZ;

COMMENT ON COLUMN public.companies.qbo_token_refreshed_at IS
    'UTC timestamp of last successful persisted QuickBooks Online OAuth token refresh.';
