-- Approval functions hash the approved payload with pgcrypto's digest().
-- On Supabase, pgcrypto lives in the "extensions" schema, but these
-- SECURITY DEFINER functions pin search_path to pg_catalog, public, pg_temp,
-- so digest() could not be found and every approval failed at runtime
-- ("function digest(bytea, unknown) does not exist").
--
-- "extensions" goes BEFORE public: it is platform-owned, so a role able to
-- create objects in public can never shadow digest() there. Where pgcrypto is
-- installed in public (plain PostgreSQL, CI) the "extensions" entry is unused.
-- No function body changes. Each function is altered only if it exists, so the
-- same file is safe in every migration root.

DO $fix$
DECLARE fn TEXT;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'secretary_internal.approve_and_queue_email(uuid,uuid,uuid,jsonb,text,jsonb)',
        'secretary_internal.approve_and_queue_purchase_order(uuid,uuid,uuid,jsonb,text,jsonb)'
    ] LOOP
        IF to_regprocedure(fn) IS NOT NULL THEN
            EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, extensions, public, pg_temp',
                           to_regprocedure(fn));
        END IF;
    END LOOP;
END
$fix$;
