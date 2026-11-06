-- Staging: restore the timestamp written during every QuickBooks Online OAuth
-- refresh. The backend must commit the rotated access/refresh tokens together
-- with their expiry and refresh time; a missing column rejects the whole write.
ALTER TABLE public.companies
    ADD COLUMN IF NOT EXISTS qbo_token_refreshed_at TIMESTAMPTZ;

COMMENT ON COLUMN public.companies.qbo_token_refreshed_at IS
    'UTC timestamp of last successful persisted QuickBooks Online OAuth token refresh.';
