"""CI-only authenticated PostgreSQL regression for company cascade permissions.

REQUIRES disposable localhost PostgreSQL17, isolated synthetic secret and
review-only SQL. Never connects to live staging or Supabase credentials.
No runtime application or end-user company records are touched.
"""
from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import asyncpg

from app.auth.registrar_rpc import create_owner_using_registrar

CI_REGISTRAR_PASSWORD = "CI_local_registrar_only_20261116"
CI_APP_ROLE_PASSWORD = "CI_local_service_role_only_20261116"


class _SyntheticRollback(Exception):
    """Rollback privileged cascade simulation in the disposable fixture."""


async def check() -> None:
    if (
        os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
        or os.environ.get("PGHOST") not in ("localhost", "127.0.0.1")
        or os.environ.get("PGUSER") != "postgres"
        or os.environ.get("PGDATABASE") != "sai"
    ):
        raise RuntimeError("Only local disposable PostgreSQL is allowed")

    host = os.environ["PGHOST"]
    port = os.environ.get("PGPORT", "5432")
    database = os.environ["PGDATABASE"]
    admin_url = (
        f"postgresql://postgres:{os.environ['PGPASSWORD']}"
        f"@{host}:{port}/{database}"
    )
    app_url = (
        f"postgresql://service_role:{CI_APP_ROLE_PASSWORD}"
        f"@{host}:{port}/{database}"
    )
    registrar_url = (
        f"postgresql://secretary_registrar:{CI_REGISTRAR_PASSWORD}"
        f"@{host}:{port}/{database}"
    )

    admin = await asyncpg.connect(admin_url, statement_cache_size=0)
    ordinary = None
    registrar = None
    company_id = None
    unique = uuid4().hex
    event_id = "ci-parent-denial-" + unique

    try:
        ordinary = await asyncpg.connect(app_url, statement_cache_size=0)
        registrar = await asyncpg.connect(registrar_url, statement_cache_size=0)
        assert await ordinary.fetchval("SELECT current_user") == "service_role"
        assert await registrar.fetchval("SELECT current_user") == "secretary_registrar"

        company = await create_owner_using_registrar(
            database_url=registrar_url,
            expected_project_ref="",
            company_name="ci-cascade-guard-" + unique,
            email=f"ci-cascade-guard-{unique}@example.invalid",
            hashed_password="$2b$12$synthetic-hash-for-disposable-only-0123456789",
            full_name="Synthetic CI owner",
        )
        company_id = UUID(company["company_id"])
        owner_id = UUID(company["id"])

        await admin.execute(
            """INSERT INTO public.logistics_events
              (event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
              VALUES ($1,$2,$3,'milestone','system_calculated',
                      '2026-11-16T00:00:00Z',$1)""",
            event_id, company_id, "ci-shipment-" + unique,
        )
        assert await admin.fetchval(
            "SELECT count(*) FROM public.companies WHERE id=$1", company_id,
        ) == 1
        assert await admin.fetchval(
            "SELECT count(*) FROM public.users WHERE id=$1 AND company_id=$2",
            owner_id, company_id,
        ) == 1

        for role in ("anon", "authenticated", "service_role", "secretary_registrar"):
            for operation in ("DELETE", "TRUNCATE"):
                assert not await admin.fetchval(
                    "SELECT has_table_privilege($1,'public.companies',$2)",
                    role, operation,
                ), (role, operation)
            for operation in ("DELETE", "UPDATE", "TRUNCATE"):
                assert not await admin.fetchval(
                    "SELECT has_table_privilege($1,'public.logistics_events',$2)",
                    role, operation,
                ), (role, operation)

        # An authentic connection as the *ordinary* service_role may not use
        # the parent-company cascade to erase tenant business/audit records.
        for query in (
            "DELETE FROM public.companies WHERE id=$1",
            "TRUNCATE public.companies",
            "DELETE FROM public.logistics_events WHERE company_id=$1",
            "TRUNCATE public.logistics_events",
        ):
            try:
                if "$1" in query:
                    await ordinary.execute(query, company_id)
                else:
                    await ordinary.execute(query)
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError(
                    "Ordinary runtime destructive operation unexpectedly worked"
                )

        # Registration identity is separate and must also lack direct DELETE.
        try:
            await registrar.execute(
                "DELETE FROM public.companies WHERE id=$1", company_id,
            )
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("Registrar had direct company DELETE")

        # Existing app Logisitics read/append remains available.
        assert await ordinary.fetchval(
            "SELECT count(*) FROM public.logistics_events WHERE event_id=$1",
            event_id,
        ) == 1
        await ordinary.execute(
            """INSERT INTO public.logistics_events
              (event_id,company_id,shipment_id,kind,source,recorded_at,idempotency_key)
              VALUES ($1,$2,$3,'milestone','system_calculated',
                      '2026-11-16T00:01:00Z',$1)""",
            "ci-app-append-" + unique, company_id, "ci-shipment-" + unique,
        )

        # Demonstrate that the independent database owner *could* still
        # perform an authorized erasure, while rolling back its example.
        # It must NOT be used as a credential inside the ordinary API process.
        try:
            async with admin.transaction():
                await admin.execute(
                    "DELETE FROM public.companies WHERE id=$1", company_id,
                )
                assert await admin.fetchval(
                    "SELECT count(*) FROM public.logistics_events WHERE company_id=$1",
                    company_id,
                ) == 0
                raise _SyntheticRollback()
        except _SyntheticRollback:
            pass

        assert await admin.fetchval(
            "SELECT count(*) FROM public.companies WHERE id=$1",
            company_id,
        ) == 1
        assert await admin.fetchval(
            "SELECT count(*) FROM public.logistics_events WHERE company_id=$1",
            company_id,
        ) == 2
        assert await admin.fetchval(
            "SELECT count(*) FROM public.users WHERE id=$1",
            owner_id,
        ) == 1
        assert await admin.fetchval(
            "SELECT count(*) FROM public.policy_rules WHERE company_id=$1",
            company_id,
        ) == 5

        print("PASS restricted_registrar_authenticates_and_runtime_parent_cascade_denied")
    finally:
        if ordinary is not None:
            await ordinary.close()
        if registrar is not None:
            await registrar.close()
        if company_id is not None:
            # Owner-only CI synthetic cleanup; entire DB is ephemeral.
            await admin.execute("DELETE FROM public.companies WHERE id=$1", company_id)
        await admin.close()


if __name__ == "__main__":
    asyncio.run(check())
