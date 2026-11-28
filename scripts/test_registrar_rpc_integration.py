"""Disposable PostgreSQL integration for the private registrar RPC.

Runs ONLY inside GitHub CI's localhost PostgreSQL 17 fixture; no Supabase
credentials, no staging/production writes and no external employee accounts.
"""
from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import asyncpg

from app.auth.registrar_rpc import create_owner_using_registrar, validate_registrar_dsn
from app.domain.policy import _default_rules

CI_REGISTRAR_PASSWORD = "CI_local_registrar_only_20261116"


async def check() -> None:
    if (os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
        or os.environ.get("PGHOST") not in ("localhost", "127.0.0.1")
        or os.environ.get("PGUSER") != "postgres"
        or os.environ.get("PGDATABASE") != "sai"):
        raise RuntimeError("Refusing registrar integration outside disposable CI")

    host = os.environ["PGHOST"]
    port = os.environ.get("PGPORT", "5432")
    db = os.environ["PGDATABASE"]
    admin_dsn = (
        f"postgresql://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}"
        f"@{host}:{port}/{db}"
    )
    reg_dsn = (
        f"postgresql://secretary_registrar:{CI_REGISTRAR_PASSWORD}"
        f"@{host}:{port}/{db}"
    )
    validate_registrar_dsn(reg_dsn)
    root = await asyncpg.connect(dsn=admin_dsn, statement_cache_size=0)
    registrar = await asyncpg.connect(dsn=reg_dsn, statement_cache_size=0)
    uuid = uuid4().hex
    email = f"registrar-ci-{uuid}@example.invalid"
    duplicate_company = f"ci-reg-duplicate-{uuid}"
    company_id = None
    try:
        # Even with this API credential, no direct company DELETE or INSERT.
        try:
            await registrar.execute(
                "DELETE FROM public.companies WHERE id=$1", uuid4()
            )
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("Registrar can execute direct company DELETE")
        try:
            await registrar.execute(
                "INSERT INTO public.companies(name) VALUES('bypass-test')"
            )
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("Registrar can execute direct company INSERT")

        result = await create_owner_using_registrar(
            database_url=reg_dsn, expected_project_ref="",
            company_name=f"ci-reg-success-{uuid}",
            email=email,
            hashed_password="$2b$12$synthetic-hash-for-disposable-only-0123456789",
            full_name="Synthetic CI owner",
        )
        company_id = UUID(result["company_id"])
        user_id = UUID(result["id"])

        assert await root.fetchval(
            "SELECT count(*) FROM public.users "
            "WHERE id=$1 AND company_id=$2 AND email=$3 AND role='owner' AND is_active",
            user_id, company_id, email,
        ) == 1
        features = await root.fetchrow(
            "SELECT computer_use_enabled, browser_auto_enabled, "
            "require_approval_above, file_ingestion_enabled "
            "FROM public.company_features WHERE company_id=$1", company_id,
        )
        assert features is not None
        assert (features["computer_use_enabled"], features["browser_auto_enabled"],
                features["file_ingestion_enabled"]) == (False, True, True)
        assert features["require_approval_above"] == 500

        stored_rules = await root.fetch(
            "SELECT rule_name, description, action_class, effect, priority, "
            "paths_match, approval_roles, approval_count "
            "FROM public.policy_rules WHERE company_id=$1", company_id,
        )
        expected = _default_rules(str(company_id))
        assert len(stored_rules) == len(expected) == 5
        actual = {row["rule_name"]: dict(row) for row in stored_rules}
        for expected_rule in expected:
            row = actual[expected_rule["rule_name"]]
            for column, value in expected_rule.items():
                if column == "company_id":
                    continue
                assert row[column] == value, (column, row[column], value)

        try:
            await create_owner_using_registrar(
                database_url=reg_dsn, expected_project_ref="",
                company_name=duplicate_company, email=email,
                hashed_password="$2b$12$synthetic-hash-for-disposable-only-0123456789",
                full_name="Duplicate CI owner",
            )
        except asyncpg.UniqueViolationError as exc:
            assert exc.constraint_name == "users_email_key"
        else:
            raise AssertionError("Duplicate registration unexpectedly committed")
        assert await root.fetchval(
            "SELECT count(*) FROM public.companies WHERE name=$1",
            duplicate_company,
        ) == 0, "Duplicate registration left a company orphan"

        print("PASS private_registrar_atomic_parity_restricted_identity_and_rollback")
    finally:
        # Only the privileged owner of this synthetic disposable database
        # cleans up its own fixture; no ordinary service_role DELETE.
        if company_id:
            await root.execute(
                "DELETE FROM public.companies WHERE id=$1", company_id,
            )
        await registrar.close()
        await root.close()


if __name__ == "__main__":
    asyncio.run(check())
