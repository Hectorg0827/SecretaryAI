#!/usr/bin/env python3
"""Generate disposable-only post-replay least-privilege grants and Auth hook SQL.

Addresses Issues #206, #215, #216, #227, and #232 on an isolated clean-replayed
Supabase branch without altering any public-schema catalog fingerprints verified
by assert_schema.sql. Never connects to a database.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from hosted_preflight import validate_disposable_ref  # noqa: E402
from hosted_replay import literal  # noqa: E402

MODES = ("finance-uat", "rls-acceptance")


def generate_post_replay_sql(project_ref: str, mode: str) -> str:
    validated_ref = validate_disposable_ref(project_ref)
    if mode not in MODES:
        raise ValueError(f"Unsupported mode {mode!r}; expected one of {MODES}")

    ref_lit = literal(validated_ref)
    assert_schema_sql = (BASE / "assert_schema.sql").read_text().rstrip("\n")

    if mode == "finance-uat":
        mode_grants_sql = """
-- Finance AR UAT least-privilege baseline (PR #208 / #216 / #206):
-- 1. Zero DELETE on companies or logistics_events for all runtime/service roles.
-- 2. Zero direct anon DML grants on tenant tables.
-- 3. Minimum service_role CRUD (no DELETE on companies/users/logistics_events) for
--    Owner auth lookup, QBO realm/token refresh, and read-only AR reconciliation.
REVOKE ALL ON TABLE public.companies, public.users, public.accounts, public.orders, public.inventory, public.conversations
  FROM PUBLIC, anon, authenticated;
REVOKE DELETE ON TABLE public.companies, public.users, public.logistics_events
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE
  public.companies,
  public.users,
  public.accounts,
  public.orders,
  public.inventory,
  public.employee_integrations
  TO service_role;
GRANT SELECT ON TABLE public.users, public.accounts, public.orders, public.inventory
  TO authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.conversations
  TO authenticated;
""".strip()
        mode_assertions_sql = """
  IF pg_catalog.has_table_privilege('service_role', 'public.companies', 'DELETE')
     OR pg_catalog.has_table_privilege('service_role', 'public.logistics_events', 'DELETE') THEN
    RAISE EXCEPTION 'Finance UAT least-privilege violation: service_role retains DELETE on companies or logistics_events';
  END IF;
  IF NOT pg_catalog.has_table_privilege('service_role', 'public.companies', 'SELECT')
     OR NOT pg_catalog.has_table_privilege('service_role', 'public.companies', 'INSERT')
     OR NOT pg_catalog.has_table_privilege('service_role', 'public.companies', 'UPDATE')
     OR NOT pg_catalog.has_table_privilege('service_role', 'public.users', 'SELECT')
     OR NOT pg_catalog.has_table_privilege('service_role', 'public.employee_integrations', 'SELECT') THEN
    RAISE EXCEPTION 'Finance UAT least-privilege violation: required service_role non-destructive grants missing';
  END IF;
  IF pg_catalog.has_table_privilege('anon', 'public.accounts', 'SELECT')
     OR pg_catalog.has_table_privilege('anon', 'public.companies', 'SELECT')
     OR pg_catalog.has_table_privilege('anon', 'public.users', 'SELECT') THEN
    RAISE EXCEPTION 'Finance UAT least-privilege violation: anon must not hold SELECT on core tenant tables';
  END IF;
""".strip()
    else:
        mode_grants_sql = """
-- Authenticated Tenant RLS Acceptance baseline (PR #232 scripts/rls_authenticated_acceptance.mjs):
-- 1. Zero DELETE on logistics_events across all roles; zero client-role access to companies.
-- 2. service_role fixture lifecycle access on companies and accounts (solely so the
--    acceptance runner's finally-block deletes synthetic RLS-ACCEPTANCE-* companies).
-- 3. authenticated CRUD on accounts + conversations and SELECT on users/orders/inventory,
--    strictly gated by existing RLS policies ((auth.jwt() ->> 'company_id')::uuid).
-- 4. anon SELECT-only on accounts so PostgREST exercises RLS empty-set filtering (HTTP 200 [])
--    with zero INSERT/UPDATE/DELETE/TRUNCATE capability.
REVOKE ALL ON TABLE public.companies, public.users, public.accounts, public.orders, public.inventory, public.conversations
  FROM PUBLIC, anon, authenticated;
REVOKE DELETE ON TABLE public.logistics_events
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.companies, public.accounts
  TO service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.users, public.orders, public.inventory, public.conversations
  TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.accounts
  TO authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.conversations
  TO authenticated;
GRANT SELECT ON TABLE public.users, public.orders, public.inventory
  TO authenticated;
GRANT SELECT ON TABLE public.accounts
  TO anon;
""".strip()
        mode_assertions_sql = """
  IF pg_catalog.has_table_privilege('service_role', 'public.logistics_events', 'DELETE') THEN
    RAISE EXCEPTION 'RLS acceptance violation: service_role retains DELETE on logistics_events';
  END IF;
  IF pg_catalog.has_table_privilege('authenticated', 'public.companies', 'SELECT')
     OR pg_catalog.has_table_privilege('authenticated', 'public.companies', 'DELETE')
     OR pg_catalog.has_table_privilege('anon', 'public.companies', 'SELECT') THEN
    RAISE EXCEPTION 'RLS acceptance violation: client role granted direct access to companies';
  END IF;
  IF pg_catalog.has_table_privilege('anon', 'public.accounts', 'INSERT')
     OR pg_catalog.has_table_privilege('anon', 'public.accounts', 'UPDATE')
     OR pg_catalog.has_table_privilege('anon', 'public.accounts', 'DELETE') THEN
    RAISE EXCEPTION 'RLS acceptance violation: anon holds write privileges on accounts';
  END IF;
""".strip()

    sql = [
        "BEGIN;",
        "SET LOCAL lock_timeout = '5s';",
        "SELECT pg_catalog.set_config('app.post_replay_xid', pg_catalog.pg_current_xact_id()::text, true);",
        f"""DO $post_replay_guard$
BEGIN
  IF current_setting('app.disposable_project_ref', true) IS DISTINCT FROM {ref_lit} THEN
    RAISE EXCEPTION 'Disposable target marker missing or mismatched for %', {ref_lit};
  END IF;
  IF EXISTS (SELECT 1 FROM auth.users) OR EXISTS (SELECT 1 FROM public.companies) THEN
    RAISE EXCEPTION 'Post-replay bootstrap requires 0 auth.users and 0 public.companies';
  END IF;
  IF (SELECT count(*) FROM supabase_migrations.schema_migrations WHERE coalesce(cardinality(statements), 0) >= 1) <> 46 THEN
    RAISE EXCEPTION 'Post-replay bootstrap requires 46 provenance-populated canonical migrations';
  END IF;
END $post_replay_guard$;""",
        assert_schema_sql,
        "-- Eliminate client-role TRUNCATE across all 78 public tables (TRUNCATE bypasses RLS; Issue #215).",
        "REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM PUBLIC, anon, authenticated;",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE TRUNCATE ON TABLES FROM PUBLIC, anon, authenticated;",
        mode_grants_sql,
        """-- Trusted GoTrue Custom Access Token Hook in secretary_internal (preserves public function catalog MD5).
-- Reads ONLY admin-controlled claims.app_metadata.company_id (never client-writable user_metadata),
-- verifies the company exists in public.companies, and withholds company_id if public.users marks
-- the user inactive or bound to another tenant.
CREATE OR REPLACE FUNCTION secretary_internal.custom_access_token_hook(event jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $auth_hook$
DECLARE
  claims jsonb := coalesce(event -> 'claims', '{}'::jsonb) - 'company_id';
  raw_company text := event #>> '{claims,app_metadata,company_id}';
  raw_user text := coalesce(event ->> 'user_id', event #>> '{claims,sub}');
  v_company_id uuid;
  v_user_id uuid;
BEGIN
  IF raw_company IS NULL
     OR raw_company !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
    RETURN jsonb_set(event, '{claims}', claims, true);
  END IF;
  v_company_id := raw_company::uuid;

  IF NOT EXISTS (SELECT 1 FROM public.companies c WHERE c.id = v_company_id) THEN
    RETURN jsonb_set(event, '{claims}', claims, true);
  END IF;

  IF raw_user IS NOT NULL
     AND raw_user ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
    v_user_id := raw_user::uuid;
    IF EXISTS (
      SELECT 1 FROM public.users u
       WHERE u.id = v_user_id
         AND (u.is_active IS DISTINCT FROM TRUE OR u.company_id IS DISTINCT FROM v_company_id)
    ) THEN
      RETURN jsonb_set(event, '{claims}', claims, true);
    END IF;
  END IF;

  claims := jsonb_set(claims, '{company_id}', to_jsonb(v_company_id::text), true);
  RETURN jsonb_set(event, '{claims}', claims, true);
END;
$auth_hook$;

REVOKE ALL ON FUNCTION secretary_internal.custom_access_token_hook(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
DO $hook_grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'supabase_auth_admin') THEN
    GRANT USAGE ON SCHEMA secretary_internal TO supabase_auth_admin;
    GRANT EXECUTE ON FUNCTION secretary_internal.custom_access_token_hook(jsonb) TO supabase_auth_admin;
  END IF;
END $hook_grant$;""",
        "-- Re-assert all 7 public-schema catalog fingerprints remain identical after post-replay grants.",
        assert_schema_sql,
        f"""DO $verify_least_privilege$
DECLARE
  client_truncate_count integer;
  unprotected_tables integer;
BEGIN
  IF current_setting('app.post_replay_xid', true) IS DISTINCT FROM pg_catalog.pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'Outer transaction continuity broken during post-replay privilege setup';
  END IF;
  SELECT count(*) INTO client_truncate_count
    FROM information_schema.role_table_grants
   WHERE table_schema = 'public'
     AND privilege_type = 'TRUNCATE'
     AND grantee IN ('PUBLIC', 'anon', 'authenticated');
  IF client_truncate_count <> 0 THEN
    RAISE EXCEPTION 'Client TRUNCATE privileges remain on % table grants', client_truncate_count;
  END IF;
  SELECT count(*) INTO unprotected_tables
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relkind IN ('r', 'p')
     AND NOT c.relrowsecurity;
  IF unprotected_tables <> 0 THEN
    RAISE EXCEPTION 'RLS disabled on % public tables', unprotected_tables;
  END IF;
  IF pg_catalog.has_function_privilege('anon', 'secretary_internal.custom_access_token_hook(jsonb)', 'EXECUTE')
     OR pg_catalog.has_function_privilege('authenticated', 'secretary_internal.custom_access_token_hook(jsonb)', 'EXECUTE')
     OR pg_catalog.has_function_privilege('service_role', 'secretary_internal.custom_access_token_hook(jsonb)', 'EXECUTE') THEN
    RAISE EXCEPTION 'custom_access_token_hook must not be executable by anon, authenticated, or service_role';
  END IF;
  {mode_assertions_sql}
  RAISE NOTICE 'PASS: post-replay least-privilege mode (%) and Auth hook verified', {literal(mode)};
END $verify_least_privilege$;""",
        "COMMIT;",
    ]
    return "\n".join(sql) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate disposable-only post-replay least-privilege and Auth hook SQL."
    )
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sql_text = generate_post_replay_sql(args.project_ref, args.mode)
    with args.output.open("x", encoding="utf-8") as f:
        f.write(sql_text)


if __name__ == "__main__":
    main()
