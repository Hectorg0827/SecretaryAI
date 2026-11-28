"""Read-only proof that a routine database credential cannot erase tenants.

A URL's username alone is not sufficient evidence of least privilege: the
authenticated *server-side* PostgreSQL principal and its effective grants
must be checked. Run this before enabling a candidate runtime identity.

No role creation, mutation, migration, DELETE or INSERT occurs here.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import asyncpg

RUNTIME_ROLE = "secretary_runtime"


@dataclass(frozen=True)
class RuntimeRoleEvidence:
    current_role: str
    session_role: str
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def validate_runtime_dsn_scope(
    database_url: str, *, expected_project_ref: str = "",
) -> bool:
    """Validate role/project BEFORE network I/O. Returns True for CI localhost.

    Never print, return, or include database_url in any exception. The Supabase
    pooler role suffix is NOT a separate authentication principal.
    """
    if not database_url or not isinstance(database_url, str):
        raise RuntimeError("Runtime database credential missing")
    try:
        parsed = urlsplit(database_url)
        host = parsed.hostname or ""
        username = unquote(parsed.username or "")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Runtime database credential invalid") from exc
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.password:
        raise RuntimeError("Runtime database credential invalid")

    if expected_project_ref:
        if not re.fullmatch(r"[a-z0-9]{20}", expected_project_ref):
            raise RuntimeError("Expected Supabase project reference invalid")
        direct = (
            host == f"db.{expected_project_ref}.supabase.co"
            and username == RUNTIME_ROLE
        )
        pooler = (
            host.endswith(".pooler.supabase.com")
            and username == f"{RUNTIME_ROLE}.{expected_project_ref}"
        )
        if not (direct or pooler):
            raise RuntimeError("Runtime database project or identity mismatch")
        return False

    if (
        os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
        or host not in ("localhost", "127.0.0.1")
        or username != RUNTIME_ROLE
    ):
        raise RuntimeError("Runtime database scope unavailable")
    return True


async def audit_runtime_database_role(
    *, database_url: str, expected_project_ref: str = "",
) -> RuntimeRoleEvidence:
    """Authenticate independently and read effective role capabilities.

    Fail on elevated authn principals and explicit/implicit permission paths.
    Supabase service_role's BYPASSRLS alone is disqualifying even if its direct
    DELETE privilege has been revoked. So is table ownership, inherited role
    membership, or a connection authenticated as postgres.
    """
    local = validate_runtime_dsn_scope(
        database_url, expected_project_ref=expected_project_ref,
    )
    try:
        conn = await asyncpg.connect(
            dsn=database_url,
            timeout=8,
            command_timeout=10,
            statement_cache_size=0,
            **({} if local else {"ssl": "require"}),
        )
    except Exception as exc:
        raise RuntimeError("Runtime database identity check unavailable") from None
    try:
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    current_user AS current_role,
                    session_user AS session_role,
                    r.rolsuper, r.rolbypassrls, r.rolcreaterole,
                    r.rolcreatedb, r.rolreplication,
                    EXISTS (
                        SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid
                    ) AS member_of_other_role,
                    (SELECT c.relowner=r.oid FROM pg_class c
                     WHERE c.oid='public.companies'::regclass) AS owns_companies,
                    (SELECT c.relowner=r.oid FROM pg_class c
                     WHERE c.oid='public.logistics_events'::regclass) AS owns_ledger,
                    has_table_privilege('public.companies','DELETE') AS company_delete,
                    has_table_privilege('public.companies','TRUNCATE') AS company_truncate,
                    has_table_privilege('public.logistics_events','DELETE') AS ledger_delete,
                    has_table_privilege('public.logistics_events','UPDATE') AS ledger_update,
                    has_table_privilege('public.logistics_events','TRUNCATE') AS ledger_truncate,
                    has_table_privilege('public.logistics_events','SELECT') AS ledger_read,
                    has_table_privilege('public.logistics_events','INSERT') AS ledger_append
                FROM pg_roles r
                WHERE r.rolname=current_user
                """
            )
        except Exception:
            raise RuntimeError("Runtime database capability query unavailable") from None
        if row is None:
            raise RuntimeError("Runtime database identity missing")
        failures: list[str] = []
        current = row["current_role"]
        session = row["session_role"]
        if current != RUNTIME_ROLE or session != RUNTIME_ROLE:
            failures.append("wrong_authenticated_principal")
        for key in (
            "rolsuper", "rolbypassrls", "rolcreaterole",
            "rolcreatedb", "rolreplication", "member_of_other_role",
            "owns_companies", "owns_ledger",
        ):
            if row[key] is not False:
                failures.append(key)
        for key in (
            "company_delete", "company_truncate", "ledger_delete",
            "ledger_update", "ledger_truncate",
        ):
            if row[key] is not False:
                failures.append(key)
        for key in ("ledger_read", "ledger_append"):
            if row[key] is not True:
                failures.append("missing_" + key)
        return RuntimeRoleEvidence(current, session, tuple(failures))
    finally:
        await conn.close()


async def require_safe_runtime_database_role(
    *, database_url: str, expected_project_ref: str = "",
) -> RuntimeRoleEvidence:
    """Fail CLOSED. Intended for a staged deployment preflight, never a fallback."""
    result = await audit_runtime_database_role(
        database_url=database_url,
        expected_project_ref=expected_project_ref,
    )
    if not result.passed:
        # This is safe to log; no raw DSN, password or SQL is exposed.
        raise RuntimeError("Routine database identity has elevated or missing grants: "
                           + ",".join(result.failures))
    return result
