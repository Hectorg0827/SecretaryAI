-- Staging-only post-baseline patch applied to Supabase project
-- xerktvdgjvjozvckjyfp on 2026-11-06 via the authorized SQL connector.
-- Keep this outside the 33-file, byte-audited migration history bundle.
-- The backend must persist rotating QuickBooks tokens atomically with their
-- expiry and this refresh timestamp.
ALTER TABLE public.companies
    ADD COLUMN IF NOT EXISTS qbo_token_refreshed_at TIMESTAMPTZ;

COMMENT ON COLUMN public.companies.qbo_token_refreshed_at IS
    'UTC timestamp of last successful persisted QuickBooks Online OAuth token refresh.';
