-- REVIEW-ONLY PROPOSAL. DO NOT DEPLOY.
-- Disposable PostgreSQL 17 CI ONLY, until independently approved as a
-- NEW CLI-generated additive migration and bundled in the correct staging path.
--
-- A private, narrowly exposed registration RPC replaces use of the general
-- DATABASE_URL superuser for onboarding. Ordinary service_role, anon, and
-- authenticated cannot call this function. No role or function in this script
-- can DELETE a company. There is NO erasure endpoint here.
--
-- Provision a separate password/secret out of band ONLY after staging review.
-- No secrets or LOGIN changes are included in this proposal.
-- All role/function/grant changes commit together; no transient PUBLIC EXECUTE.
BEGIN;

DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'secretary_registrar') THEN
    CREATE ROLE secretary_registrar NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER NOREPLICATION;
  END IF;
END
$role$;

-- Do not inherit tenant table grants. Public schema remains unavailable for
-- data modifications to this identity; it can only EXECUTE the private RPC.
GRANT USAGE ON SCHEMA secretary_internal TO secretary_registrar;
REVOKE ALL PRIVILEGES ON TABLE
  public.companies, public.users, public.company_features, public.policy_rules
  FROM secretary_registrar;

CREATE OR REPLACE FUNCTION secretary_internal.register_owner_atomic(
  p_company_name text,
  p_email text,
  p_hashed_password text,
  p_full_name text
)
RETURNS TABLE(company_id uuid, user_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $register$
DECLARE
  v_company uuid;
  v_user uuid;
BEGIN
  -- The API owns password and email syntax validation; this function enforces
  -- a minimal independently verified boundary, and never accepts a caller-
  -- supplied tenant UUID or role.
  IF session_user <> 'secretary_registrar' THEN
    RAISE EXCEPTION 'Registration role unavailable'
      USING ERRCODE = '42501';
  END IF;
  IF p_company_name IS NULL OR length(btrim(p_company_name)) = 0
     OR length(p_company_name) > 250
     OR p_email IS NULL OR length(p_email) > 320
     OR p_email !~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
     OR p_hashed_password IS NULL OR length(p_hashed_password) < 30
  THEN
    RAISE EXCEPTION 'Invalid registration fields'
      USING ERRCODE = '22023';
  END IF;

  -- One function invocation = one PG statement transaction. Any failed
  -- feature, policy or user INSERT unwinds the whole function call.
  INSERT INTO public.companies(name) VALUES (p_company_name)
    RETURNING id INTO v_company;

  INSERT INTO public.company_features
    (company_id, computer_use_enabled, browser_auto_enabled,
     require_approval_above, file_ingestion_enabled)
  VALUES (v_company, FALSE, TRUE, 500.00, TRUE);

  -- Mirror app.domain.policy._default_rules, enforced by a parity CI test.
  INSERT INTO public.policy_rules
    (company_id, rule_name, description, action_class, effect,
     priority, paths_match, approval_roles, approval_count)
  VALUES
    (v_company, 'allow_all_reads',
     'Read-only data access is always allowed.',
     'read', 'allow', 10, NULL, ARRAY['owner','manager']::text[], 1),
    (v_company, 'allow_drafts',
     'Drafts (pending approval) can be created without pre-approval.',
     'draft', 'allow', 20, NULL, ARRAY['owner','manager']::text[], 1),
    (v_company, 'require_approval_commits',
     'Any committed action requires manager or owner approval.',
     'commit', 'require_approval', 50, NULL, ARRAY['owner','manager']::text[], 1),
    (v_company, 'deny_destructive',
     'Destructive actions are denied unless overridden.',
     'destructive', 'deny', 60, NULL, ARRAY['owner','manager']::text[], 1),
    (v_company, 'deny_computer_use_commits',
     'Commit actions executed via computer-use are denied; only API and browser paths may commit.',
     'commit', 'deny', 40, ARRAY['computer_use']::text[], ARRAY['owner','manager']::text[], 1);

  INSERT INTO public.users(
    company_id, email, hashed_password, full_name, role, is_active
  ) VALUES (
    v_company, p_email, p_hashed_password, nullif(p_full_name,''), 'owner', TRUE
  ) RETURNING id INTO v_user;

  RETURN QUERY SELECT v_company, v_user;
END
$register$;

-- Postgres defaults new functions to EXECUTE for PUBLIC; explicitly remove it.
REVOKE ALL ON FUNCTION secretary_internal.register_owner_atomic(text,text,text,text)
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION secretary_internal.register_owner_atomic(text,text,text,text)
  TO secretary_registrar;

COMMIT;

-- No direct company DELETE is revoked by this proposal. That permission will
-- be removed through a separate reviewed migration ONLY after an independent
-- erasure/admin identity, legal-hold controls, out-of-band audit and QA exist.
