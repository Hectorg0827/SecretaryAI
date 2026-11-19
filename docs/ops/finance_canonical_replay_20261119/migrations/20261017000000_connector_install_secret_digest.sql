-- Phase 4 / defect #55. Run before deploying the digest-only connector API.
-- Idempotent on existing installations. pgcrypto is available on Supabase.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER TABLE public.companies
    ADD COLUMN IF NOT EXISTS connector_install_secret TEXT;

-- Migrate legacy plaintext in-place without ever copying it to another table.
-- SHA-256 is appropriate for 256-bit randomly-generated install secrets.
UPDATE public.companies
SET connector_install_secret = 'sha256:' ||
    encode(digest(connector_install_secret, 'sha256'), 'hex')
WHERE connector_install_secret IS NOT NULL
  AND connector_install_secret NOT LIKE 'sha256:%';

-- Future application writes must be digests. Null means unprovisioned.
ALTER TABLE public.companies
    DROP CONSTRAINT IF EXISTS connector_install_secret_digest_format;
ALTER TABLE public.companies
    ADD CONSTRAINT connector_install_secret_digest_format CHECK (
        connector_install_secret IS NULL OR
        connector_install_secret ~ '^sha256:[0-9a-f]{64}$'
    );
