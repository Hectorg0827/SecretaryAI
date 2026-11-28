"""Atomic owner registration for Lion BI.

Only the server-side registration endpoint may call this.  It performs the
company, safety-defaults, policy-rule and owner inserts on ONE PostgreSQL
connection and transaction.  Never issue a DELETE to compensate for partial
registration through the ordinary Supabase service role.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable
from uuid import UUID

import asyncpg

# SQL column names come ONLY from these trusted, code-defined policy templates.
# Template values are always passed as bind parameters.
_POLICY_COLUMNS = frozenset({
    "company_id", "rule_name", "description", "action_class", "effect",
    "min_amount", "max_amount", "roles_match", "paths_match",
    "capabilities_match", "approval_roles", "approval_count", "priority",
})


async def create_company_owner_transactionally(
    *,
    database_url: str,
    company_name: str,
    email: str,
    hashed_password: str,
    full_name: str,
    policy_rules_factory: Callable[[str], list[dict]],
) -> dict[str, str]:
    """Create a complete and usable tenant, or commit absolutely nothing.

    Uses the existing backend-only DATABASE_URL, never a browser/service_role
    PostgREST DELETE call.  If the database is unavailable or a policy seed
    fails, asyncpg's transaction context rolls everything back.  Disabling
    the statement cache permits Supabase transaction pooler connections.
    """
    connection = await asyncpg.connect(
        dsn=database_url, timeout=8, command_timeout=15,
        statement_cache_size=0,
    )
    try:
        async with connection.transaction():
            company_id = await connection.fetchval(
                "INSERT INTO public.companies (name) VALUES ($1) RETURNING id",
                company_name,
            )
            if not isinstance(company_id, UUID):
                raise RuntimeError("Company insert returned no valid ID")

            await connection.execute(
                """
                INSERT INTO public.company_features
                    (company_id, computer_use_enabled, browser_auto_enabled,
                     require_approval_above, file_ingestion_enabled)
                VALUES ($1, $2, $3, $4, $5)
                """,
                company_id, False, True, Decimal("500.00"), True,
            )

            # Reuse the existing PolicyEngine templates, rather than maintain
            # two competing sets of default grants, limits or actions.
            rules = policy_rules_factory(str(company_id))
            if not isinstance(rules, list) or not rules:
                raise RuntimeError("Registration policy defaults unavailable")
            for rule in rules:
                if not isinstance(rule, dict) or not rule:
                    raise RuntimeError("Invalid registration policy template")
                columns = tuple(rule.keys())
                if (set(columns) - _POLICY_COLUMNS or
                    not {"company_id", "rule_name", "action_class", "effect"}.issubset(columns) or
                    rule["company_id"] != str(company_id)):
                    raise RuntimeError("Untrusted registration policy template")
                placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
                values = [company_id if key == "company_id" else rule[key] for key in columns]
                await connection.execute(
                    f"INSERT INTO public.policy_rules ({', '.join(columns)}) "
                    f"VALUES ({placeholders})",
                    *values,
                )

            user_id = await connection.fetchval(
                """
                INSERT INTO public.users
                    (company_id, email, hashed_password, full_name,
                     role, is_active)
                VALUES ($1, $2, $3, $4, 'owner', TRUE)
                RETURNING id
                """,
                company_id, email, hashed_password, full_name or None,
            )
            if not isinstance(user_id, UUID):
                raise RuntimeError("Owner insert returned no valid ID")
            return {"id": str(user_id), "company_id": str(company_id)}
    finally:
        await connection.close()
