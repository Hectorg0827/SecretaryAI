-- Approval functions hash the approved payload with pgcrypto's digest().
-- On Supabase, pgcrypto lives in the "extensions" schema, but these
-- SECURITY DEFINER functions pin search_path to pg_catalog, public, pg_temp,
-- so digest() could not be found and every approval failed at runtime
-- ("function digest(bytea, unknown) does not exist").
--
-- Add "extensions" AFTER public (it is owned by the platform and not writable
-- by tenant roles). Where pgcrypto is installed in public (plain PostgreSQL,
-- CI) the extra entry is simply unused. No function body changes.

ALTER FUNCTION secretary_internal.approve_and_queue_email(UUID, UUID, UUID, JSONB, TEXT, JSONB)
    SET search_path = pg_catalog, public, extensions, pg_temp;

ALTER FUNCTION secretary_internal.approve_and_queue_purchase_order(UUID, UUID, UUID, JSONB, TEXT, JSONB)
    SET search_path = pg_catalog, public, extensions, pg_temp;
