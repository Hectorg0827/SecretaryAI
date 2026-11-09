"""
Record-level filter for the legacy UnifiedDataAdapter.

Existing modules (the original /api/chat pipeline in particular) read customers
and invoices straight from the adapter and handed the whole company's data to
the model — so a sales rep could ask about any account. Wrapping the adapter
with ``ScopedAdapter`` applies Lola's record-level rules to those reads without
rewriting each module. Company-wide principals get the adapter unchanged.
"""
from __future__ import annotations

from typing import Any

from app.lola.authorization import Authorizer


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


class ScopedAdapter:
    _CUSTOMER_LISTS = ("get_all_customers",)
    _INVOICE_LISTS = ("get_orders_last_n_days", "get_invoice_summaries_last_n_days")

    def __init__(self, adapter: Any, authorizer: Authorizer):
        self._adapter = adapter
        self._auth = authorizer

    async def get_all_customers(self, *args, **kwargs):
        rows = await self._adapter.get_all_customers(*args, **kwargs)
        return [c for c in rows if self._auth.can_see_account(_get(c, "qb_id"))]

    async def get_orders_last_n_days(self, *args, **kwargs):
        rows = await self._adapter.get_orders_last_n_days(*args, **kwargs)
        return [i for i in rows if self._auth.can_see_account(_get(i, "customer_id"))]

    async def get_invoice_summaries_last_n_days(self, *args, **kwargs):
        rows = await self._adapter.get_invoice_summaries_last_n_days(*args, **kwargs)
        return [i for i in rows if self._auth.can_see_account(_get(i, "customer_id"))]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


def scope_adapter(adapter: Any, authorizer: Authorizer) -> Any:
    if adapter is None or authorizer.account_scope().company_wide:
        return adapter
    return ScopedAdapter(adapter, authorizer)
