"""BusinessDataGateway: the only path Lola BI uses to read business data.

Guarantees
----------
* **Tenant isolation** – the connector must belong to the caller's tenant;
  cached snapshots are keyed and re-checked by tenant.
* **Least privilege** – a source is not read at all unless the caller's scope
  holds a capability that needs it (``DENIED`` snapshot, no provider call).
* **Record scope** – customer-bound records (customers, invoices, payments,
  assignments, correspondence) are filtered to the caller's assigned accounts
  on *every* return, including cache hits. Cost fields are removed for callers
  without cost visibility.
* **Truthful failures** – an exception becomes a non-usable snapshot with a
  sanitized error code (``auth_failed``, ``not_configured`` …), never ``[]``.
* **Read-only** – no write method of any connector is referenced.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping

import httpx

from app.connectors.qb_bi_reads import SourceNotSupported, bi_reads_for
from app.lola_bi.contracts import (
    SourceQuality, SourceSnapshot, SourceStatus, TenantMismatch, utcnow,
)
from app.lola_bi.scope import BIAccessScope, Cap

log = logging.getLogger(__name__)

_ANY = None  # any BI capability may read the source

# Capabilities that justify reading each source.
SOURCE_CAPS: dict[str, frozenset[str] | None] = {
    "qb.company_currency": _ANY,
    "qb.customers": _ANY,
    "qb.products": _ANY,
    "qb.open_invoices": frozenset({Cap.AR_COMPANY, Cap.AR_OWN, Cap.FINANCE_CASHFLOW, Cap.CRM_READ,
                                   Cap.SALES_COMPANY, Cap.SALES_OWN, Cap.SALES_TEAM, Cap.FINANCE_ANOMALIES}),
    "qb.invoices": frozenset({Cap.SALES_COMPANY, Cap.SALES_OWN, Cap.SALES_TEAM, Cap.FINANCE_PROFITABILITY,
                              Cap.PRICING_READ, Cap.CRM_READ, Cap.AR_COMPANY, Cap.AR_OWN,
                              Cap.FINANCE_ANOMALIES}),
    "qb.payments": frozenset({Cap.AR_COMPANY, Cap.AR_OWN, Cap.CRM_READ}),
    "qb.open_bills": frozenset({Cap.AP_READ, Cap.AP_PROPOSE, Cap.FINANCE_CASHFLOW, Cap.FINANCE_ANOMALIES}),
    "qb.bank_accounts": frozenset({Cap.FINANCE_CASH, Cap.FINANCE_CASHFLOW, Cap.AP_PROPOSE}),
    "qb.profit_and_loss": frozenset({Cap.FINANCE_PNL}),
    "inventory.merged": frozenset({Cap.INVENTORY_READ}),
    "db.account_directory": frozenset({Cap.SALES_TEAM, Cap.SALES_COMPANY, Cap.SALES_OWN, Cap.CRM_READ}),
    "mail.messages": frozenset({Cap.COMMS_METADATA}),
}

# Records carrying a customer id that must be scoped for assigned-account callers.
_CUSTOMER_KEY = {
    "qb.customers": "id",
    "qb.open_invoices": "customer_id",
    "qb.invoices": "customer_id",
    "qb.payments": "customer_id",
    "db.account_directory": "customer_id",
}

_DEFAULT_TTL = timedelta(minutes=2)


class SnapshotCache:
    """Tenant-keyed cache of *unfiltered* snapshots. Scope filtering happens on read."""

    def __init__(self, ttl: timedelta = _DEFAULT_TTL):
        self._ttl = ttl
        self._entries: dict[tuple[str, str], tuple[datetime, SourceSnapshot]] = {}

    def get(self, tenant_id: str, key: str, now: datetime) -> SourceSnapshot | None:
        entry = self._entries.get((tenant_id, key))
        if entry is None:
            return None
        stored_at, snapshot = entry
        if snapshot.tenant_id != tenant_id:
            raise TenantMismatch("Cache entry tenant mismatch")
        if now - stored_at > self._ttl:
            del self._entries[(tenant_id, key)]
            return None
        return snapshot

    def put(self, tenant_id: str, key: str, snapshot: SourceSnapshot, now: datetime) -> None:
        if snapshot.tenant_id != tenant_id:
            raise TenantMismatch("Refusing to cache another tenant's snapshot")
        if snapshot.usable:
            self._entries[(tenant_id, key)] = (now, snapshot)

    def clear(self) -> None:
        self._entries.clear()


def classify_error(exc: BaseException) -> tuple[SourceStatus, str]:
    """Map an exception to a status and a sanitized code (no provider text)."""
    if isinstance(exc, SourceNotSupported):
        return SourceStatus.NOT_SUPPORTED, "not_supported_by_provider"
    if isinstance(exc, httpx.HTTPStatusError):
        host = exc.request.url.host if exc.request is not None else ""
        if "oauth" in host:
            return SourceStatus.AUTH_FAILED, "oauth_refresh_failed"
        code = exc.response.status_code if exc.response is not None else 0
        if code in (401, 403):
            return SourceStatus.AUTH_FAILED, "provider_rejected_credentials"
        if code == 429:
            return SourceStatus.FAILED, "provider_rate_limited"
        return SourceStatus.FAILED, f"provider_http_{code}"
    if isinstance(exc, httpx.TransportError):
        return SourceStatus.FAILED, "provider_unreachable"
    message = str(exc).lower()
    if isinstance(exc, RuntimeError) and "token refresh" in message:
        return SourceStatus.AUTH_FAILED, "oauth_refresh_not_persisted"
    if isinstance(exc, RuntimeError) and "no qb connector" in message:
        return SourceStatus.NOT_CONFIGURED, "quickbooks_not_connected"
    if isinstance(exc, RuntimeError) and "no email connector" in message:
        return SourceStatus.NOT_CONFIGURED, "mailbox_not_connected"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return SourceStatus.FAILED, "malformed_source_data"
    return SourceStatus.FAILED, "source_error"


class BusinessDataGateway:
    def __init__(
        self, adapter: Any, scope: BIAccessScope, *, db: Any = None, reads: Any = None,
        cache: SnapshotCache | None = None, clock: Callable[[], datetime] = utcnow,
    ):
        tenant = getattr(adapter, "_config", {}).get("id") if adapter is not None else None
        if tenant != scope.tenant_id:
            raise TenantMismatch("Connector does not belong to the caller's tenant")
        self._adapter = adapter
        self._scope = scope
        self._db = db
        self._reads = reads if reads is not None else bi_reads_for(adapter)
        self._cache = cache if cache is not None else SnapshotCache()
        self._clock = clock

    @property
    def scope(self) -> BIAccessScope:
        return self._scope

    @property
    def provider(self) -> str:
        return getattr(self._reads, "provider", "quickbooks")

    def _quality(self) -> SourceQuality:
        return SourceQuality(getattr(self._reads, "mapping_quality", "provider_observed"))

    # ── core read wrapper ────────────────────────────────────────────────────

    async def _read(
        self, source: str, key: str, call: Callable[[], Awaitable[Any]], *,
        provider: str | None = None, coverage: str, window: tuple[date, date] | None = None,
        status: SourceStatus = SourceStatus.OK, quality: SourceQuality | None = None,
        warnings: tuple[str, ...] = (), max_age: timedelta = timedelta(hours=1),
        single: bool = False,
    ) -> SourceSnapshot:
        tenant = self._scope.tenant_id
        provider = provider or self.provider
        needed = SOURCE_CAPS[source]
        if needed is not None and not self._scope.has(*needed):
            return SourceSnapshot.failed(tenant, source, provider, SourceStatus.DENIED, "outside_caller_scope")
        if needed is None and not self._scope.capabilities:
            return SourceSnapshot.failed(tenant, source, provider, SourceStatus.DENIED, "outside_caller_scope")

        now = self._clock()
        cached = self._cache.get(tenant, key, now)
        if cached is not None:
            return self._restrict(cached)

        win_from, win_to = window if window else (None, None)
        try:
            payload = await call()
            if single:
                if not isinstance(payload, Mapping):
                    raise ValueError("Source returned a non-mapping record")
                records: tuple = (dict(payload),)
            else:
                if not isinstance(payload, (list, tuple)):
                    raise ValueError("Source returned a non-list payload")
                if any(not isinstance(r, Mapping) for r in payload):
                    raise ValueError("Source returned a non-mapping record")
                records = tuple(dict(r) for r in payload)
            snapshot = SourceSnapshot(
                tenant_id=tenant, source=source, provider=provider, status=status,
                records=records, fetched_at=now, coverage=coverage,
                window_from=win_from, window_to=win_to,
                quality=quality or self._quality(), warnings=warnings, max_age=max_age,
            )
        except Exception as exc:  # noqa: BLE001 — classified, never swallowed as empty
            code_status, code = classify_error(exc)
            log.warning("Lola BI source read failed")
            return SourceSnapshot.failed(tenant, source, provider, code_status, code,
                                         coverage=coverage, window_from=win_from, window_to=win_to)
        self._cache.put(tenant, key, snapshot, now)
        return self._restrict(snapshot)

    def _restrict(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        if snapshot.tenant_id != self._scope.tenant_id:
            raise TenantMismatch("Snapshot tenant mismatch")
        records = snapshot.records
        key = _CUSTOMER_KEY.get(snapshot.source)
        if key and not self._scope.company_wide:
            records = tuple(r for r in records if self._scope.permits_account(r.get(key)))
        if snapshot.source == "qb.products" and not self._scope.can_view_costs:
            records = tuple({**r, "cost": None, "cost_hidden": True} for r in records)
        return dataclasses.replace(snapshot, records=records)

    def _need_reads(self, method: str) -> Callable[..., Awaitable[Any]]:
        if self._reads is None:
            async def missing(*_a: Any, **_k: Any) -> Any:
                raise RuntimeError("No QB connector configured")
            return missing
        return getattr(self._reads, method)

    # ── QuickBooks sources ──────────────────────────────────────────────────

    async def company_currency(self) -> SourceSnapshot:
        return await self._read("qb.company_currency", "currency",
                                lambda: self._need_reads("company_currency")(),
                                coverage="company_settings", single=True, max_age=timedelta(days=1))

    async def customers(self) -> SourceSnapshot:
        return await self._read("qb.customers", "customers", lambda: self._need_reads("customers")(),
                                coverage="active_customers")

    async def products(self) -> SourceSnapshot:
        return await self._read("qb.products", "products", lambda: self._need_reads("products")(),
                                coverage="inventory_items")

    async def open_invoices(self) -> SourceSnapshot:
        return await self._read("qb.open_invoices", "open_invoices",
                                lambda: self._need_reads("open_invoices")(),
                                coverage="all_open_invoices")

    async def invoices(self, date_from: date, date_to: date) -> SourceSnapshot:
        if date_from > date_to:
            raise ValueError("Invoice window starts after it ends")
        return await self._read(
            "qb.invoices", f"invoices:{date_from}:{date_to}",
            lambda: self._need_reads("invoices_between")(date_from, date_to),
            coverage="date_window", window=(date_from, date_to),
        )

    async def payments(self, date_from: date, date_to: date) -> SourceSnapshot:
        if date_from > date_to:
            raise ValueError("Payment window starts after it ends")
        return await self._read(
            "qb.payments", f"payments:{date_from}:{date_to}",
            lambda: self._need_reads("payments_between")(date_from, date_to),
            coverage="date_window", window=(date_from, date_to),
        )

    async def open_bills(self) -> SourceSnapshot:
        return await self._read("qb.open_bills", "open_bills", lambda: self._need_reads("open_bills")(),
                                coverage="all_open_bills")

    async def bank_accounts(self) -> SourceSnapshot:
        return await self._read("qb.bank_accounts", "bank_accounts",
                                lambda: self._need_reads("bank_accounts")(),
                                coverage="active_bank_accounts", max_age=timedelta(minutes=30))

    async def profit_and_loss(self, start: date, end: date) -> SourceSnapshot:
        if start > end:
            raise ValueError("P&L period starts after it ends")
        return await self._read(
            "qb.profit_and_loss", f"pnl:{start}:{end}",
            lambda: self._need_reads("profit_and_loss")(start, end),
            coverage="accounting_report", window=(start, end), single=True,
        )

    # ── other SecretaryAI sources ───────────────────────────────────────────

    async def inventory(self) -> SourceSnapshot:
        async def call() -> list:
            return await self._adapter.get_inventory_merged()
        # Merged rows are QB-authoritative with explicit partial lineage (WS2 M3).
        return await self._read("inventory.merged", "inventory", call, provider="secretaryai_inventory",
                                coverage="current_stock", status=SourceStatus.PARTIAL,
                                quality=SourceQuality.PROVIDER_OBSERVED)

    async def account_directory(self) -> SourceSnapshot:
        tenant = self._scope.tenant_id

        async def call() -> list:
            if self._db is None:
                raise RuntimeError("Account assignment store not configured")
            result = (self._db.table("accounts")
                      .select("qb_customer_id,name,territory,assigned_rep_id,company_id")
                      .eq("company_id", tenant).execute())
            rows = getattr(result, "data", None)
            if not isinstance(rows, list):
                raise ValueError("Account directory response malformed")
            rep_names: dict[str, str] = {}
            if self._scope.has(Cap.SALES_TEAM):
                users = (self._db.table("users").select("id,full_name,company_id")
                         .eq("company_id", tenant).execute())
                for user in getattr(users, "data", None) or []:
                    if isinstance(user, Mapping) and user.get("company_id") == tenant and user.get("id"):
                        rep_names[str(user["id"])] = user.get("full_name") or ""
            out = []
            for row in rows:
                if not isinstance(row, Mapping) or row.get("company_id") != tenant:
                    raise TenantMismatch("Account directory returned another tenant's row")
                cid = row.get("qb_customer_id")
                if isinstance(cid, str) and cid.strip():
                    rep = row.get("assigned_rep_id") or None
                    out.append({
                        "customer_id": cid.strip(), "name": row.get("name") or "",
                        "territory": (row.get("territory") or "").strip() or None,
                        "assigned_rep_id": str(rep) if rep else None,
                        "assigned_rep_name": rep_names.get(str(rep)) if rep else None,
                    })
            return out
        return await self._read("db.account_directory", "account_directory", call,
                                provider="secretaryai_db", coverage="assignments",
                                quality=SourceQuality.TENANT_DECLARED)

    async def messages(self, query: str = "", max_results: int = 50) -> SourceSnapshot:
        """Correspondence *metadata* only (no bodies/snippets)."""
        provider = ("gmail" if getattr(self._adapter, "_gmail", None) is not None
                    else "outlook" if getattr(self._adapter, "_outlook", None) is not None else "mailbox")
        max_results = max(1, min(int(max_results), 200))

        async def call() -> list:
            rows = await self._adapter.get_emails(query=query, max_results=max_results)
            if not isinstance(rows, list):
                raise ValueError("Mailbox returned a non-list payload")
            keep = ("id", "thread_id", "from", "from_email", "subject", "date")
            return [{k: r.get(k) for k in keep} for r in rows if isinstance(r, Mapping)]
        return await self._read("mail.messages", f"mail:{query}:{max_results}", call, provider=provider,
                                coverage="recent_messages_sample", status=SourceStatus.PARTIAL,
                                warnings=("mailbox results are a bounded recent sample",))
