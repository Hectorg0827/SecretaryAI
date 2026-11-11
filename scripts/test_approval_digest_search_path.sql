-- Both approval functions must resolve digest() whether pgcrypto lives in
-- public (plain PostgreSQL) or in an "extensions" schema (Supabase).
BEGIN;
DO $t$
DECLARE cfg TEXT[];
BEGIN
    FOR cfg IN
        SELECT p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'secretary_internal'
           AND p.proname IN ('approve_and_queue_email', 'approve_and_queue_purchase_order')
    LOOP
        IF NOT ('search_path=pg_catalog, public, extensions, pg_temp' = ANY(cfg)) THEN
            RAISE EXCEPTION 'approval function search_path not fixed: %', cfg;
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'secretary_internal'
           AND p.proname IN ('approve_and_queue_email', 'approve_and_queue_purchase_order')) <> 2 THEN
        RAISE EXCEPTION 'approval functions missing';
    END IF;
END
$t$;

-- Simulate the Supabase layout: pgcrypto moved to "extensions".
CREATE SCHEMA IF NOT EXISTS extensions;
ALTER EXTENSION pgcrypto SET SCHEMA extensions;
SET LOCAL search_path = pg_catalog, public, pg_temp;
DO $t$
BEGIN
    IF to_regprocedure('digest(bytea,text)') IS NOT NULL THEN
        RAISE EXCEPTION 'test setup: digest should not resolve without extensions';
    END IF;
END
$t$;
SET LOCAL search_path = pg_catalog, public, extensions, pg_temp;
DO $t$
BEGIN
    IF to_regprocedure('digest(bytea,text)') IS NULL THEN
        RAISE EXCEPTION 'digest does not resolve with the fixed search_path';
    END IF;
END
$t$;
ROLLBACK;
\echo '✓ Approval digest search_path checks passed.'
