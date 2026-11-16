"""Candidate isolated registration transport. NOT wired to the live API.

Calls a private, reviewed PostgreSQL function using ONLY the registrar's
dedicated credentials. The registrar must not have DELETE or any direct table
DML privileges; it can only execute secretary_internal.register_owner_atomic.

No staging/production provisioning or deployment is authorized by this file.
"""
from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

import asyncpg


def validate_registrar_dsn(database_url: str, *, expected_project_ref: str = "") -> None:
    """Fail closed on superuser, normal API-role, pooler or cross-project mixups."""
    if not isinstance(database_url, str) or not database_url:
        raise RuntimeError("Dedicated registrar database credentials unavailable")
    try:
        url = urlsplit(database_url)
        host = url.hostname or ""
        user = url.username or ""
    except ValueError as exc:
        raise RuntimeError("Dedicated registrar database URL malformed") from exc

    if url.scheme not in ("postgres", "postgresql") or not url.password:
        raise RuntimeError("Dedicated registrar database URL invalid")
    if expected_project_ref:
        direct = (
            host == f"db.{expected_project_ref}.supabase.co"
            and user == "secretary_registrar"
        )
        pooler = (
            host.endswith(".pooler.supabase.com")
            and user == f"secretary_registrar.{expected_project_ref}"
        )
        if not direct and not pooler:
            raise RuntimeError("Dedicated registrar database project or role mismatch")
    elif host not in ("localhost", "127.0.0.1") or user != "secretary_registrar":
        # Only disposable localhost CI may omit a target Supabase project ref.
        raise RuntimeError("Expected Supabase project reference for registrar connection")


async def create_owner_using_registrar(
    *, database_url: str, expected_project_ref: str,
    company_name: str, email: str, hashed_password: str, full_name: str,
) -> dict[str, str]:
    """One call into the server-owned, limited-scope registration RPC.

    Does not accept a caller-chosen company_id/role, does not expose a DELETE,
    and never falls back to the general-purpose DATABASE_URL.
    """
    validate_registrar_dsn(database_url, expected_project_ref=expected_project_ref)
    conn = await asyncpg.connect(
        dsn=database_url, timeout=8, command_timeout=15,
        statement_cache_size=0,
    )
    try:
        row = await conn.fetchrow(
            "SELECT company_id, user_id "
            "FROM secretary_internal.register_owner_atomic($1,$2,$3,$4)",
            company_name, email, hashed_password, full_name,
        )
        if row is None or not isinstance(row["company_id"], UUID) or not isinstance(row["user_id"], UUID):
            raise RuntimeError("Registrar returned an invalid registration receipt")
        return {"company_id": str(row["company_id"]), "id": str(row["user_id"])}
    finally:
        await conn.close()
