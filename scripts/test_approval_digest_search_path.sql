-- Both approval functions must resolve digest() whether pgcrypto lives in
-- public (plain PostgreSQL) or in an "extensions" schema (Supabase), and the
-- platform-owned "extensions" schema must come before public.
BEGIN;
DO $t$
DECLARE fn TEXT; cfg TEXT[];
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'secretary_internal.approve_and_queue_email(uuid,uuid,uuid,jsonb,text,jsonb)',
        'secretary_internal.approve_and_queue_purchase_order(uuid,uuid,uuid,jsonb,text,jsonb)'
    ] LOOP
        IF to_regprocedure(fn) IS NULL THEN
            RAISE EXCEPTION 'approval function missing: %', fn;
        END IF;
        SELECT proconfig INTO cfg FROM pg_proc WHERE oid = to_regprocedure(fn);
        -- NULL config (no pinned path) must fail, not pass silently.
        IF cfg IS NULL OR NOT coalesce('search_path=pg_catalog, extensions, public, pg_temp' = ANY(cfg), FALSE) THEN
            RAISE EXCEPTION 'approval function % has search_path %', fn, cfg;
        END IF;
    END LOOP;
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
-- Resolve under each function's own stored path, not a hand-written one.
DO $t$
DECLARE fn TEXT; path TEXT;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'secretary_internal.approve_and_queue_email(uuid,uuid,uuid,jsonb,text,jsonb)',
        'secretary_internal.approve_and_queue_purchase_order(uuid,uuid,uuid,jsonb,text,jsonb)'
    ] LOOP
        SELECT substring(c FROM '^search_path=(.*)$') INTO path
          FROM pg_proc, unnest(proconfig) AS c
         WHERE oid = to_regprocedure(fn) AND c LIKE 'search_path=%';
        PERFORM set_config('search_path', path, TRUE);
        IF to_regprocedure('digest(bytea,text)') IS NULL THEN
            RAISE EXCEPTION 'digest does not resolve under % stored path %', fn, path;
        END IF;
        IF (SELECT n.nspname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.oid = to_regprocedure('digest(bytea,text)')) <> 'extensions' THEN
            RAISE EXCEPTION 'digest must resolve to the extensions schema';
        END IF;
    END LOOP;
END
$t$;
ROLLBACK;
\echo '✓ Approval digest search_path checks passed.'
