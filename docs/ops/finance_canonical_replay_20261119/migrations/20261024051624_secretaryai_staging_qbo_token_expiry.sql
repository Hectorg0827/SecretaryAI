-- Track QBO access token expiry so the background refresh task knows which
-- tokens need to be renewed proactively.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS qbo_token_expires_at TIMESTAMPTZ;

COMMENT ON COLUMN companies.qbo_token_expires_at IS
    'UTC timestamp when the QBO access token expires. '
    'Updated on every token exchange or refresh. '
    'Used by the qbo_token_refresh Celery task.';
