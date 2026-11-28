-- Disposable CI ONLY. Assert that dedicated registrar can only call private RPC.
-- A green result is a role-contract check, not staging deployment approval.
DO $guard$
DECLARE
  reg_oid oid;
  fn_oid oid;
  rol text;
  tbl text;
  operation text;
BEGIN
  SELECT oid INTO reg_oid FROM pg_roles WHERE rolname = 'secretary_registrar';
  IF reg_oid IS NULL THEN
    RAISE EXCEPTION 'Missing dedicated registrar role';
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE oid=reg_oid
            AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolinherit OR rolcreaterole)) THEN
    RAISE EXCEPTION 'Registrar role is not a disabled least-privilege identity';
  END IF;
  fn_oid := to_regprocedure(
    'secretary_internal.register_owner_atomic(text,text,text,text)'
  )::oid;
  IF fn_oid IS NULL THEN
    RAISE EXCEPTION 'Registration function missing';
  END IF;
  IF NOT has_function_privilege('secretary_registrar', fn_oid, 'EXECUTE') THEN
    RAISE EXCEPTION 'Dedicated registrar cannot execute registration RPC';
  END IF;
  FOREACH rol IN ARRAY ARRAY['service_role','anon','authenticated'] LOOP
    IF has_function_privilege(rol,fn_oid,'EXECUTE') THEN
      RAISE EXCEPTION 'Routine application role has registrar EXECUTE: %',rol;
    END IF;
  END LOOP;
  FOREACH tbl IN ARRAY ARRAY['companies','users','company_features','policy_rules'] LOOP
    FOREACH operation IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE'] LOOP
      IF has_table_privilege('secretary_registrar',('public.'||tbl)::regclass,operation) THEN
        RAISE EXCEPTION 'Unexpected registrar table privilege %.%',tbl,operation;
      END IF;
    END LOOP;
  END LOOP;
  IF EXISTS(
    SELECT 1 FROM pg_proc WHERE oid=fn_oid
      AND (prosecdef = FALSE
           OR NOT ('search_path=pg_catalog, pg_temp' = ANY(coalesce(proconfig,ARRAY[]::text[])))
          )
  ) THEN
    RAISE EXCEPTION 'Registration function is not pinned and definer-scoped';
  END IF;
  IF has_schema_privilege('authenticated','secretary_internal','USAGE')
     OR has_schema_privilege('anon','secretary_internal','USAGE') THEN
    RAISE EXCEPTION 'Private registration schema exposed to client roles';
  END IF;
END
$guard$;

SELECT 'PASS private_registrar_no_table_dml_and_no_app_role_execute' AS result;
