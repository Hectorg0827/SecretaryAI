#!/usr/bin/env python3
"""Read-only hosted preflight and target-binding validator for disposable branches."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess

DEFAULT_DISPOSABLE_REF = "ontqkqgofkghsdgtqwsm"
REF = DEFAULT_DISPOSABLE_REF

FORBIDDEN_PROJECT_REFS = frozenset({
    "xerktvdgjvjozvckjyfp",  # Shared SecretaryAI-Staging parent (PROTECTED)
    "gpeytsazslopipmplixe",  # Protected project ref
    "toujmmvlgktprabjbccq",  # Existing 52-version Finance UAT branch
    "djfcginvavwuyfxxlyjv",  # Historical broken Finance UAT branch
    "keopiqwmcrftydjvztev",  # Historical registrar preview branch
})

EXPECTED_BASELINE_VERSIONS = (
    "20240101000000",
    "20240601000000",
    "20240901000000",
    "20261017000000",
    "20261017010000",
)

PREFLIGHT_SQL = """
DO $preflight$
DECLARE
  db_name text := current_database();
  public_tables integer;
  auth_users integer;
  total_versions integer;
  unexpected_versions integer;
BEGIN
  IF db_name <> 'postgres' THEN
    RAISE EXCEPTION 'Unexpected database % (expected postgres)', db_name;
  END IF;
  SELECT count(*) INTO public_tables FROM pg_catalog.pg_tables WHERE schemaname = 'public';
  IF public_tables <> 0 THEN
    RAISE EXCEPTION 'Disposable preflight failed: expected 0 public tables, found %', public_tables;
  END IF;
  SELECT count(*) INTO auth_users FROM auth.users;
  IF auth_users <> 0 THEN
    RAISE EXCEPTION 'Disposable preflight failed: expected 0 auth.users, found %', auth_users;
  END IF;
  SELECT count(*) INTO total_versions FROM supabase_migrations.schema_migrations;
  SELECT count(*) INTO unexpected_versions
  FROM supabase_migrations.schema_migrations
  WHERE version NOT IN ('20240101000000','20240601000000','20240901000000','20261017000000','20261017010000')
     OR coalesce(cardinality(statements), 0) <> 0;
  IF total_versions <> 5 OR unexpected_versions <> 0 THEN
    RAISE EXCEPTION 'Disposable preflight failed: expected 5 empty baseline versions, found total=% unexpected=%',
      total_versions, unexpected_versions;
  END IF;
  RAISE NOTICE 'PASS: disposable target verified read-only (0 public tables, 0 auth.users, 5 empty baseline versions)';
END $preflight$;
""".strip()


def validate_disposable_ref(project_ref: str) -> str:
    """Validate that project_ref is an explicitly designated disposable branch ref."""
    if not isinstance(project_ref, str) or not re.fullmatch(r"[a-z]{20}", project_ref):
        raise ValueError("Project ref must be exactly 20 lowercase ASCII letters")
    parent_env = os.environ.get("SUPABASE_PARENT_PROJECT_REF", "").strip()
    if project_ref in FORBIDDEN_PROJECT_REFS or (parent_env and project_ref == parent_env):
        raise ValueError(f"Protected or non-clean project ref is forbidden: {project_ref}")
    expected = os.environ.get("EXPECTED_DISPOSABLE_PROJECT_REF", "").strip() or DEFAULT_DISPOSABLE_REF
    if expected in FORBIDDEN_PROJECT_REFS or (parent_env and expected == parent_env):
        raise ValueError(f"EXPECTED_DISPOSABLE_PROJECT_REF points to a forbidden project: {expected}")
    if not re.fullmatch(r"[a-z]{20}", expected):
        raise ValueError("EXPECTED_DISPOSABLE_PROJECT_REF must be 20 lowercase ASCII letters")
    if project_ref != expected:
        raise ValueError(
            f"Project ref {project_ref} does not match designated disposable ref {expected}"
        )
    return project_ref


def host_for_ref(project_ref: str) -> str:
    validated = validate_disposable_ref(project_ref)
    return f"db.{validated}.supabase.co"


HOST = f"db.{DEFAULT_DISPOSABLE_REF}.supabase.co"


def connection_env(password: str | None, ca: str | None, project_ref: str = DEFAULT_DISPOSABLE_REF) -> dict[str, str]:
    target_host = host_for_ref(project_ref)
    if not password or not ca or not os.path.isfile(ca):
        raise ValueError("Branch password and trusted CA file required")
    return {
        "PATH": os.defpath,
        "PGHOST": target_host,
        "PGPORT": "5432",
        "PGDATABASE": "postgres",
        "PGUSER": "postgres",
        "PGPASSWORD": password,
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": ca,
        "PGCONNECT_TIMEOUT": "10",
        "PGOPTIONS": "-c default_transaction_read_only=on",
    }


def resolve_psql_binary() -> str:
    psql_bin = shutil.which("psql")
    if not psql_bin:
        raise RuntimeError("psql client binary not found on PATH")
    return psql_bin


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only preflight for an empty disposable Supabase branch.")
    p.add_argument("--project-ref", required=True)
    args = p.parse_args()
    ref = validate_disposable_ref(args.project_ref)
    env = connection_env(
        os.environ.get("DISPOSABLE_DB_PASSWORD"),
        os.environ.get("DISPOSABLE_DB_CA"),
        project_ref=ref,
    )
    psql_bin = resolve_psql_binary()
    subprocess.run(
        [psql_bin, "-X", "-w", "-v", "ON_ERROR_STOP=1", "-c", PREFLIGHT_SQL],
        env=env,
        check=True,
        timeout=30,
    )


if __name__ == "__main__":
    main()
