"""Server-side access scope for Lola BI tools.

The scope is resolved from a *verified* JWT payload (``get_current_user``) plus
server-held account assignments. It is never taken from a request body.

Workstream 1 owns authentication and the tenant policy authority. This module
exposes ``BIScopeProvider`` so WS1 can supply its own resolver; the default
``AssignmentScopeProvider`` derives capabilities from the existing
``app.auth.rbac.ROLE_PERMISSIONS`` role set and assignments from the
``accounts`` table (``assigned_rep_id``/``territory``). Claims may only
*narrow* capabilities, never widen them. Any failure resolves to an error,
never to company-wide access.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app.lola_bi.contracts import BIAccessDenied


class Cap:
    """BI capabilities. Each department tool requires one of these."""

    FINANCE_CASH = "finance.cash"
    FINANCE_PNL = "finance.pnl"
    FINANCE_CASHFLOW = "finance.cashflow"
    FINANCE_PROFITABILITY = "finance.profitability"
    FINANCE_ANOMALIES = "finance.anomalies"
    AR_COMPANY = "ar.company"
    AR_OWN = "ar.own_accounts"
    AP_READ = "ap.read"
    AP_PROPOSE = "ap.propose_payments"
    PRICING_READ = "pricing.read"
    SALES_COMPANY = "sales.company"
    SALES_OWN = "sales.own_accounts"
    SALES_TEAM = "sales.team"
    CRM_READ = "crm.read"
    INVENTORY_READ = "inventory.read"
    COMMS_METADATA = "comms.metadata"
    COVERAGE_READ = "bi.coverage"

    ALL = frozenset({
        FINANCE_CASH, FINANCE_PNL, FINANCE_CASHFLOW, FINANCE_PROFITABILITY,
        FINANCE_ANOMALIES, AR_COMPANY, AR_OWN, AP_READ, AP_PROPOSE, PRICING_READ,
        SALES_COMPANY, SALES_OWN, SALES_TEAM, CRM_READ, INVENTORY_READ,
        COMMS_METADATA, COVERAGE_READ,
    })


# Default role → capability grants. Department staff (back_office) see the
# operational ledgers they work (AR, AP, company sales) but not cash, P&L,
# product costs/margins or payment proposals. Reps see only their accounts and
# never company mailbox metadata or costs.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "owner": Cap.ALL,
    "manager": Cap.ALL,
    "back_office": frozenset({
        Cap.AR_COMPANY, Cap.AR_OWN, Cap.AP_READ, Cap.SALES_COMPANY, Cap.SALES_OWN,
        Cap.CRM_READ, Cap.INVENTORY_READ, Cap.COMMS_METADATA, Cap.COVERAGE_READ,
    }),
    "sales_rep": frozenset({
        Cap.SALES_OWN, Cap.AR_OWN, Cap.CRM_READ, Cap.INVENTORY_READ,
    }),
    "viewer": frozenset(),
}

# Roles whose record scope is the whole tenant. Everyone else is limited to
# server-assigned accounts; unknown roles get nothing.
_COMPANY_WIDE_ROLES = frozenset({"owner", "manager", "back_office"})
_COST_VISIBLE_ROLES = frozenset({"owner", "manager"})


class ScopeUnavailable(RuntimeError):
    """Assignments could not be loaded; the caller must not fall back to all."""


@dataclass(frozen=True)
class BIAccessScope:
    tenant_id: str
    user_id: str
    role: str
    capabilities: frozenset[str]
    record_scope: str  # "company" | "assigned_accounts"
    allowed_account_ids: frozenset[str] = field(default_factory=frozenset)
    territories: frozenset[str] = field(default_factory=frozenset)
    can_view_costs: bool = False

    def __post_init__(self) -> None:
        for name in ("tenant_id", "user_id", "role"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise BIAccessDenied(f"Scope missing {name}")
        if self.record_scope not in ("company", "assigned_accounts"):
            raise BIAccessDenied("Unknown record scope")
        unknown = set(self.capabilities) - Cap.ALL
        if unknown:
            raise BIAccessDenied("Scope contains unknown capabilities")

    @property
    def company_wide(self) -> bool:
        return self.record_scope == "company"

    def has(self, *caps: str) -> bool:
        return any(cap in self.capabilities for cap in caps)

    def require(self, *caps: str) -> None:
        if not self.has(*caps):
            raise BIAccessDenied("This request is outside your permitted business data")

    def permits_account(self, account_id: object) -> bool:
        if not isinstance(account_id, str) or not account_id:
            return False
        return self.company_wide or account_id in self.allowed_account_ids

    def fingerprint(self) -> str:
        raw = "|".join([
            self.tenant_id, self.user_id, self.role, self.record_scope,
            ",".join(sorted(self.capabilities)), ",".join(sorted(self.allowed_account_ids)),
            str(self.can_view_costs),
        ])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def describe(self) -> dict:
        return {
            "role": self.role,
            "record_scope": self.record_scope,
            "assigned_account_count": None if self.company_wide else len(self.allowed_account_ids),
            "cost_data_visible": self.can_view_costs,
        }


class BIScopeProvider(Protocol):
    """WS1 may implement this against its policy authority."""

    async def resolve(self, user: Mapping[str, Any]) -> BIAccessScope: ...


def _narrow(role_caps: frozenset[str], user: Mapping[str, Any]) -> frozenset[str]:
    claimed = user.get("bi_capabilities")
    if claimed is None:
        return role_caps
    if not isinstance(claimed, (list, tuple, set, frozenset)):
        return frozenset()
    return role_caps & frozenset(c for c in claimed if isinstance(c, str))


class AssignmentScopeProvider:
    """Default resolver: role grants + ``accounts`` assignment rows."""

    def __init__(self, db: Any):
        self._db = db

    async def resolve(self, user: Mapping[str, Any]) -> BIAccessScope:
        tenant_id = user.get("company_id")
        user_id = user.get("sub")
        role = user.get("role")
        if not all(isinstance(v, str) and v.strip() for v in (tenant_id, user_id, role)):
            raise BIAccessDenied("Verified tenant, user and role are required")
        caps = _narrow(ROLE_CAPABILITIES.get(role, frozenset()), user)
        if not caps:
            raise BIAccessDenied("Role has no business-intelligence access")

        if role in _COMPANY_WIDE_ROLES:
            return BIAccessScope(
                tenant_id=tenant_id, user_id=user_id, role=role, capabilities=caps,
                record_scope="company", can_view_costs=role in _COST_VISIBLE_ROLES,
            )

        allowed, territories = self._load_assignments(tenant_id, user_id)
        return BIAccessScope(
            tenant_id=tenant_id, user_id=user_id, role=role, capabilities=caps,
            record_scope="assigned_accounts", allowed_account_ids=allowed,
            territories=territories, can_view_costs=False,
        )

    def _load_assignments(self, tenant_id: str, user_id: str) -> tuple[frozenset[str], frozenset[str]]:
        try:
            result = (
                self._db.table("accounts")
                .select("qb_customer_id,territory,assigned_rep_id,company_id")
                .eq("company_id", tenant_id)
                .eq("assigned_rep_id", user_id)
                .execute()
            )
        except Exception as exc:
            raise ScopeUnavailable("Account assignments unavailable") from exc
        rows = getattr(result, "data", None)
        if not isinstance(rows, list):
            raise ScopeUnavailable("Account assignments response malformed")
        allowed: set[str] = set()
        territories: set[str] = set()
        for row in rows:
            # Re-check every row: a misbehaving query must not widen scope.
            if not isinstance(row, dict):
                raise ScopeUnavailable("Account assignment row malformed")
            if row.get("company_id") != tenant_id or row.get("assigned_rep_id") != user_id:
                continue
            customer_id = row.get("qb_customer_id")
            if isinstance(customer_id, str) and customer_id.strip():
                allowed.add(customer_id.strip())
                territory = row.get("territory")
                if isinstance(territory, str) and territory.strip():
                    territories.add(territory.strip())
        return frozenset(allowed), frozenset(territories)
