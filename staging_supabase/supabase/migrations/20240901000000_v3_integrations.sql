-- SecretaryAI V3 Integration Columns
-- Run AFTER schema_v2_additions.sql.
-- Adds timestamp tracking for OAuth connections.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS qbo_connected_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS google_connected_at    TIMESTAMPTZ;
