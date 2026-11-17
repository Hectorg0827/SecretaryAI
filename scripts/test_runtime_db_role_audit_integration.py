"""Disposable PG17 test of the *authenticated* ordinary runtime principal.

This test never connects to the internet or any Supabase project, and never
prints credentials. It verifies a real role, real effective privileges, and
fail-closed handling of a newly granted dangerous DELETE or BYPASSRLS.
"""
from __future__ import annotations

import asyncio
import os

import asyncpg

from app.auth.runtime_role_audit import (
    audit_runtime_database_role,
    require_safe_runtime_database_role,
    validate_runtime_dsn_scope,
)


async def check() -> None:
    if (
        os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
        or os.environ.get("PGHOST") not in ("localhost", "127.0.0.1")
        or os.environ.get("PGUSER") != "postgres"
        or os.environ.get("PGDATABASE") != "sai"
    ):
        raise RuntimeError("Only disposable localhost PostgreSQL is permitted")
    host = os.environ["PGHOST"]
    port = os.environ.get("PGPORT", "5432")
    db = os.environ["PGDATABASE"]
    admin_dsn = f"postgresql://postgres:{os.environ['PGPASSWORD']}@{host}:{port}/{db}"
    safe_dsn = (
        "postgresql://secretary_runtime:CI_runtime_identity_only_20261116"
        f"@{host}:{port}/{db}"
    )
    assert validate_runtime_dsn_scope(safe_dsn)
    for unsafe in (
        admin_dsn,
        f"postgresql://service_role:any@{host}:{port}/{db}",
        "postgresql://secretary_runtime:test@not-a-localhost.example.invalid/sai",
    ):
        try:
            validate_runtime_dsn_scope(unsafe)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Unsafe database URL unexpectedly passed preflight")

    admin = await asyncpg.connect(admin_dsn, statement_cache_size=0)
    try:
        await admin.execute(
            "CREATE ROLE secretary_runtime LOGIN NOINHERIT NOSUPERUSER "
            "NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION "
            "PASSWORD 'CI_runtime_identity_only_20261116'"
        )
        await admin.execute("GRANT USAGE ON SCHEMA public TO secretary_runtime")
        await admin.execute("GRANT SELECT ON public.companies TO secretary_runtime")
        await admin.execute(
            "GRANT SELECT, INSERT ON public.logistics_events TO secretary_runtime"
        )

        actual = await require_safe_runtime_database_role(database_url=safe_dsn)
        assert actual.passed
        assert actual.current_role == actual.session_role == "secretary_runtime"

        await admin.execute("GRANT DELETE ON public.companies TO secretary_runtime")
        bad = await audit_runtime_database_role(database_url=safe_dsn)
        assert "company_delete" in bad.failures, bad.failures
        try:
            await require_safe_runtime_database_role(database_url=safe_dsn)
        except RuntimeError as exc:
            assert "company_delete" in str(exc)
            assert "CI_runtime_identity_only_20261116" not in str(exc)
        else:
            raise AssertionError("Unsafe runtime grant was accepted")
        await admin.execute("REVOKE DELETE ON public.companies FROM secretary_runtime")

        await admin.execute("ALTER ROLE secretary_runtime BYPASSRLS")
        bad = await audit_runtime_database_role(database_url=safe_dsn)
        assert "rolbypassrls" in bad.failures, bad.failures
        await admin.execute("ALTER ROLE secretary_runtime NOBYPASSRLS")

        assert (await require_safe_runtime_database_role(database_url=safe_dsn)).passed
        print("PASS authenticated_runtime_identity_read_only_audit_rejects_DELETE_and_BYPASSRLS")
    finally:
        await admin.execute(
            "REVOKE SELECT,INSERT,DELETE,UPDATE,TRUNCATE ON "
            "public.logistics_events FROM secretary_runtime"
        )
        await admin.execute(
            "REVOKE SELECT,DELETE,TRUNCATE ON public.companies "
            "FROM secretary_runtime"
        )
        await admin.execute("REVOKE USAGE ON SCHEMA public FROM secretary_runtime")
        await admin.execute("DROP ROLE secretary_runtime")
        await admin.close()


if __name__ == "__main__":
    asyncio.run(check())
