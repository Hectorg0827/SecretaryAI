"""
SecretaryAI · Feature Factory — Host App Wiring
===============================================
Real implementations of the four `SEAM:` dependencies in router.py, bound to
SecretaryAI's existing pieces:

  * SEAM 1 (get_db)              -> asyncpg pool over DATABASE_URL
  * SEAM 2 (get_current_user_id) -> JWT 'sub' from app.auth.rbac
  * SEAM 3 (get_tenant_id)       -> JWT 'company_id' (mapped to ff_*.tenant_id)
  * SEAM 4 (get_data_gateway /
            get_effect_executor) -> CompanyDataGateway / CompanyEffectExecutor
                                    against the existing orders/accounts/inventory
                                    /drafts/action_log tables.

The router is mounted in app/main.py and these are installed via
`app.dependency_overrides`, so the upstream module stays portable.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg
from fastapi import Depends

from app.auth.rbac import get_current_user
from app.config import get_settings

from .engine import BusinessDataGateway, EffectExecutor
from .repository import DBExecutor

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# asyncpg pool (one per process, lazily initialized)
# ---------------------------------------------------------------------------
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            get_settings().database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


class AsyncpgExecutor:
    """Adapts asyncpg.Pool to the Feature Factory's DBExecutor protocol."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        rows = await self._pool.fetch(query, *args)
        return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> Optional[dict]:
        row = await self._pool.fetchrow(query, *args)
        return dict(row) if row else None

    async def execute(self, query: str, *args: Any) -> Any:
        return await self._pool.execute(query, *args)


# ---------------------------------------------------------------------------
# Dependency implementations (installed via dependency_overrides in main.py)
# ---------------------------------------------------------------------------
async def real_get_db() -> DBExecutor:
    return AsyncpgExecutor(await get_pool())


def real_get_current_user_id(user: dict = Depends(get_current_user)) -> UUID:
    return UUID(user["sub"])


def real_get_tenant_id(user: dict = Depends(get_current_user)) -> UUID:
    # In SecretaryAI a "tenant" is a company. The ff_* tables store this
    # company_id verbatim in their tenant_id columns.
    return UUID(user["company_id"])


# ---------------------------------------------------------------------------
# Business data gateway — reads existing tables, scoped to one company.
# ---------------------------------------------------------------------------
class CompanyDataGateway:
    """
    Maps the Feature Factory's source entities onto SecretaryAI's existing tables:
      invoices  -> orders (qb_invoice_id-bearing rows)
      orders    -> orders
      customers -> accounts
      inventory -> inventory
      payments  -> orders with outstanding balance
    Every query is hard-scoped by company_id.
    """

    def __init__(self, pool: asyncpg.Pool, tenant_id: UUID):
        self._pool = pool
        self._tenant_id = tenant_id

    async def fetch_records(
        self, entity: str, source_filter: dict[str, Any], tenant_id: UUID
    ) -> list[dict[str, Any]]:
        # The caller (RuleEngine) passes the same tenant_id we were constructed
        # with; assert defensively so a mis-wire is loud, not silent.
        assert tenant_id == self._tenant_id, "tenant mismatch in data gateway"

        if entity == "invoices":
            rows = await self._pool.fetch(
                """
                SELECT id, qb_invoice_id AS ref, total_amount AS amount,
                       balance, status, order_date, due_date
                  FROM orders WHERE company_id = $1
                """,
                tenant_id,
            )
        elif entity == "orders":
            rows = await self._pool.fetch(
                """
                SELECT id, qb_invoice_id AS ref, total_amount AS amount,
                       status, order_date, due_date
                  FROM orders WHERE company_id = $1
                """,
                tenant_id,
            )
        elif entity == "customers":
            rows = await self._pool.fetch(
                """
                SELECT id, qb_customer_id AS ref, name, health_status
                  FROM accounts WHERE company_id = $1
                """,
                tenant_id,
            )
        elif entity == "inventory":
            rows = await self._pool.fetch(
                """
                SELECT id, qb_item_id AS ref, product_name,
                       total_qty, weeks_remaining, stock_status
                  FROM inventory WHERE company_id = $1
                """,
                tenant_id,
            )
        elif entity == "payments":
            # No dedicated payments table; surface unpaid invoices.
            rows = await self._pool.fetch(
                """
                SELECT id, qb_invoice_id AS ref, balance AS amount, due_date
                  FROM orders WHERE company_id = $1 AND balance > 0
                """,
                tenant_id,
            )
        else:
            log.warning("CompanyDataGateway: unknown entity %s", entity)
            return []

        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Effect executor — writes effects into the existing action_log / drafts tables.
# ---------------------------------------------------------------------------
class CompanyEffectExecutor:
    """
    Performs the side effect for a given action.
      * read/draft actions  -> called directly by the RuleEngine in ACT mode
      * commit/destructive  -> called by the Service ONLY after human approval
    Everything lands in action_log (or drafts for emails) so it shows up in the
    existing review UIs and is never hidden.
    """

    def __init__(self, pool: asyncpg.Pool, tenant_id: UUID):
        self._pool = pool
        self._tenant_id = tenant_id

    async def perform(
        self, action_type: str, params: dict[str, Any],
        record: dict[str, Any], tenant_id: UUID,
    ) -> dict[str, Any]:
        ref = str(record.get("ref") or record.get("id") or "")

        if action_type == "draft_email":
            await self._pool.execute(
                """
                INSERT INTO drafts (company_id, created_by, action_type, content, status)
                VALUES ($1, 'feature_factory', 'draft_customer_email', $2, 'pending')
                """,
                tenant_id,
                json.dumps({
                    "ref": ref,
                    "subject": params.get("subject", ""),
                    "body": params.get("body", ""),
                }),
            )
            return {"ok": True, "wrote": "drafts"}

        if action_type == "summarize":
            # Read-only by definition — nothing to persist here.
            return {"ok": True, "wrote": "noop"}

        # flag / tag / create_task / notify all land in action_log with a
        # feature_factory.* action_type so they're filterable.
        await self._pool.execute(
            """
            INSERT INTO action_log
              (company_id, actor, action_type, autonomy_level, description,
               data_involved, status)
            VALUES ($1, 'feature_factory', $2, 'notify', $3, $4, 'executed')
            """,
            tenant_id,
            f"feature_factory.{action_type}",
            f"{action_type} {ref}",
            json.dumps({"ref": ref, **params}),
        )
        return {"ok": True, "wrote": "action_log"}


async def real_get_data_gateway(
    user: dict = Depends(get_current_user),
) -> BusinessDataGateway:
    return CompanyDataGateway(await get_pool(), UUID(user["company_id"]))


async def real_get_effect_executor(
    user: dict = Depends(get_current_user),
) -> EffectExecutor:
    return CompanyEffectExecutor(await get_pool(), UUID(user["company_id"]))


def install_overrides(app) -> None:
    """
    Replace the router.py SEAM stubs with the real implementations above.
    Called once at app startup from app/main.py.
    """
    from . import router as ff_router

    app.dependency_overrides[ff_router.get_db] = real_get_db
    app.dependency_overrides[ff_router.get_current_user_id] = real_get_current_user_id
    app.dependency_overrides[ff_router.get_tenant_id] = real_get_tenant_id
    app.dependency_overrides[ff_router.get_data_gateway] = real_get_data_gateway
    app.dependency_overrides[ff_router.get_effect_executor] = real_get_effect_executor
