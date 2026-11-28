"""Disposable PostgreSQL acceptance for atomic Lion BI owner registration.

Run ONLY inside GitHub CI's PostgreSQL 17 service after schema validation.
Never points at Supabase, staging, production, or a non-local database.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg

from app.auth.registration import create_company_owner_transactionally


def fixture_rules(company_id: str) -> list[dict]:
    """Small policy subset: actual production templates are unit-tested separately."""
    return [
        {"company_id": company_id, "rule_name": "ci_allow_reads", "description": "Synthetic fixture",
         "action_class": "read", "effect": "allow", "priority": 10},
        {"company_id": company_id, "rule_name": "ci_deny_destructive",
         "action_class": "destructive", "effect": "deny", "priority": 60},
    ]


def reject_policy_seed(_: str) -> list[dict]:
    raise RuntimeError("ci fail before owner insert")


async def check() -> None:
    if (os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1" or
        os.environ.get("PGHOST") not in ("localhost", "127.0.0.1") or
        os.environ.get("PGDATABASE") != "sai" or
        os.environ.get("PGUSER") != "postgres"):
        raise RuntimeError("Refusing to run registration integration outside local disposable CI")

    dsn = (
        f"postgresql://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}"
        f"@{os.environ['PGHOST']}:{os.environ.get('PGPORT', '5432')}"
        f"/{os.environ['PGDATABASE']}"
    )
    unique = uuid4().hex
    success_name = f"ci-atomic-success-{unique}"
    duplicate_name = f"ci-atomic-duplicate-{unique}"
    failed_name = f"ci-atomic-failed-{unique}"
    email = f"atomic-{unique}@example.invalid"
    conn = await asyncpg.connect(dsn=dsn, statement_cache_size=0)
    company_id = None

    try:
        record = await create_company_owner_transactionally(
            database_url=dsn, company_name=success_name, email=email,
            hashed_password="ci-synthetic-hash-not-a-login-secret",
            full_name="CI disposable owner", policy_rules_factory=fixture_rules,
        )
        company_id = record["company_id"]
        assert await conn.fetchval(
            "SELECT count(*) FROM public.companies WHERE id=$1",
            __import__("uuid").UUID(company_id),
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM public.users WHERE company_id=$1 AND email=$2 AND role='owner' AND is_active",
            __import__("uuid").UUID(company_id), email,
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM public.company_features WHERE company_id=$1 AND computer_use_enabled=false",
            __import__("uuid").UUID(company_id),
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM public.policy_rules WHERE company_id=$1",
            __import__("uuid").UUID(company_id),
        ) == 2

        try:
            await create_company_owner_transactionally(
                database_url=dsn, company_name=duplicate_name, email=email,
                hashed_password="ci-synthetic-hash-not-a-login-secret",
                full_name="Duplicate test", policy_rules_factory=fixture_rules,
            )
        except asyncpg.UniqueViolationError as exc:
            if exc.constraint_name != "users_email_key":
                raise
        else:
            raise AssertionError("Duplicate owner unexpectedly committed")
        assert await conn.fetchval(
            "SELECT count(*) FROM public.companies WHERE name=$1", duplicate_name,
        ) == 0, "Duplicate email left an orphan tenant"

        try:
            await create_company_owner_transactionally(
                database_url=dsn, company_name=failed_name,
                email=f"failed-{unique}@example.invalid",
                hashed_password="ci-synthetic-hash-not-a-login-secret",
                full_name="Failed test", policy_rules_factory=reject_policy_seed,
            )
        except RuntimeError as exc:
            if "ci fail before owner insert" not in str(exc):
                raise
        else:
            raise AssertionError("Policy failure unexpectedly committed")
        assert await conn.fetchval(
            "SELECT count(*) FROM public.companies WHERE name=$1", failed_name,
        ) == 0, "Policy seed exception left an orphan tenant"

        print("PASS atomic_registration_commit_duplicate_and_seed_failure_rollback")
    finally:
        # CI synthetic test owner cleanup, never an application service-role call.
        # This entire database service is disposable after the GitHub job.
        if company_id is not None:
            await conn.execute(
                "DELETE FROM public.companies WHERE id=$1",
                __import__("uuid").UUID(company_id),
            )
        await conn.close()


if __name__ == "__main__":
    asyncio.run(check())
