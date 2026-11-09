"""
Bridges that make Workstreams 2 and 3 run INSIDE Lola's security model.

* Workstream 2 (``app.lola_bi``) asked WS1 for a policy-backed
  ``BIScopeProvider``. :class:`LolaBIScopeProvider` builds the BI scope from the
  Lola principal — role re-read from the database, org hierarchy, territories,
  delegations and financial clearance — instead of the role cached in the JWT.
* Workstream 3 (logistics) builds a ``LogisticsViewer`` from the JWT.
  :func:`logistics_viewer_for` builds it from the same principal, and fills the
  sales-rep ``customer_ids`` from Lola's account scope (previously an optional
  JWT claim, so reps saw nothing).
* :class:`BITool` and :class:`LogisticsAssistantTool` expose both as ordinary
  Lola tools, so the orchestrator authorises them before any read and wraps
  their output in the shared ``ToolResult`` contract.

Every tool is double-gated: Lola's capability check runs first (a denied tool
never touches a source), then the workstream's own capability/scope check.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.lola.authorization import (
    AuthorizationDenied,
    AuthorizationUnavailable,
    Authorizer,
    Principal,
    build_principal,
)
from app.lola.contracts import Department, EvidenceRef, LolaStatus, ResourceType, Sensitivity
from app.lola.tools import LolaTool, ToolContext, ToolOutput, ToolUnavailable

log = logging.getLogger(__name__)


# ─── Workstream 2: business intelligence ─────────────────────────────────────

_FINANCE_CAPS = ("finance.cash", "finance.pnl", "finance.cashflow", "finance.profitability",
                 "finance.anomalies")
_COST_CAPS = ("pricing.read", "ap.propose_payments")


def bi_scope_for(principal: Principal, authorizer: Authorizer):
    """Lola principal → WS2 ``BIAccessScope`` (can only be narrower than WS2's role defaults,
    except that an explicit financial clearance grants read-only finance views)."""
    from app.lola_bi.scope import ROLE_CAPABILITIES, BIAccessScope, Cap

    caps = set(ROLE_CAPABILITIES.get(principal.role, frozenset()))
    scope = authorizer.account_scope()
    if not principal.has_financial_access:
        caps -= set(_FINANCE_CAPS) | set(_COST_CAPS)
    elif principal.financial_clearance:
        # An owner-granted clearance (e.g. finance staff) reads finance views; it
        # never adds payment proposals.
        caps |= set(_FINANCE_CAPS) | {Cap.AR_COMPANY}
    if not scope.company_wide:
        # Department managers and reps: their records only, never company totals.
        caps -= {Cap.SALES_COMPANY, Cap.AR_COMPANY, Cap.COMMS_METADATA}
        if principal.is_department_manager:
            caps |= {Cap.SALES_OWN, Cap.AR_OWN} & set(ROLE_CAPABILITIES.get(principal.role, frozenset()))
    return BIAccessScope(
        tenant_id=principal.company_id,
        user_id=principal.user_id,
        role=principal.role,
        capabilities=frozenset(caps),
        record_scope="company" if scope.company_wide else "assigned_accounts",
        allowed_account_ids=frozenset() if scope.company_wide else frozenset(scope.visible_ids),
        territories=frozenset(principal.territories),
        can_view_costs=principal.has_financial_access,
    )


class LolaBIScopeProvider:
    """WS1's policy-backed resolver for WS2 (``app.api.lola_bi.get_scope_provider``)."""

    def __init__(self, db: Any):
        self._db = db

    async def resolve(self, user: Mapping[str, Any]):
        from app.lola_bi.contracts import BIAccessDenied
        from app.lola_bi.scope import ScopeUnavailable

        try:
            principal = build_principal(self._db, str(user.get("company_id") or ""), str(user.get("sub") or ""))
        except AuthorizationDenied as exc:
            raise BIAccessDenied("User is not an active member of this company") from exc
        except AuthorizationUnavailable as exc:
            raise ScopeUnavailable("Authorization source unavailable") from exc
        auth = Authorizer(principal, self._db)
        if not auth.account_scope().assignments_available and not auth.account_scope().company_wide:
            raise ScopeUnavailable("Account assignments unavailable")
        scope = bi_scope_for(principal, auth)
        if not scope.capabilities:
            raise BIAccessDenied("Role has no business-intelligence access")
        return scope


_BI_DEPARTMENT = {
    "finance": (Department.FINANCE, ResourceType.FINANCIALS, Sensitivity.CONFIDENTIAL),
    "accounts_receivable": (Department.FINANCE, ResourceType.SALES, Sensitivity.INTERNAL),
    "accounts_payable": (Department.PURCHASING, ResourceType.PURCHASING, Sensitivity.CONFIDENTIAL),
    "pricing": (Department.SALES, ResourceType.INVENTORY_COST, Sensitivity.CONFIDENTIAL),
    "sales_crm": (Department.SALES, ResourceType.SALES, Sensitivity.INTERNAL),
    "cross_system": (Department.GENERAL, ResourceType.CUSTOMER_ACCOUNT, Sensitivity.INTERNAL),
}
_BI_STATUS = {
    "ok": LolaStatus.ANSWERED, "partial": LolaStatus.PARTIAL, "unavailable": LolaStatus.UNAVAILABLE,
    "denied": LolaStatus.DENIED, "invalid_request": LolaStatus.UNAVAILABLE,
}


def _finding_text(env: dict) -> str:
    findings = env.get("findings") or []
    lines = [str(f.get("statement")) for f in findings if isinstance(f, dict) and f.get("statement")]
    if lines:
        return " ".join(lines[:3])
    missing = (env.get("data_quality") or {}).get("missing_or_limited") or []
    if missing:
        return "Not available: " + ", ".join(str(m.get("item")) for m in missing[:3] if isinstance(m, dict))
    return f"{env.get('tool')}: {env.get('status')}"


class BITool(LolaTool):
    """One Workstream 2 tool, run through Lola."""

    def __init__(self, bi_name: str, department: str):
        dept, resource, sensitivity = _BI_DEPARTMENT.get(department, _BI_DEPARTMENT["cross_system"])
        self.bi_name = bi_name
        self.name = f"bi.{bi_name}"
        self.department = dept
        self.resource = resource
        self.sensitivity = sensitivity
        self.description = f"Workstream 2 business intelligence: {bi_name}"

    async def fetch(self, ctx: ToolContext, args: dict, decision) -> ToolOutput:
        from app.lola_bi.contracts import TenantMismatch
        from app.lola_bi.gateway import BusinessDataGateway
        from app.lola_bi.tools import LolaBIService

        scope = bi_scope_for(ctx.principal, ctx.authorizer)
        if not scope.capabilities:
            raise ToolUnavailable("No business-intelligence access for your role")
        try:
            gateway = BusinessDataGateway(ctx.adapter, scope, db=ctx.db)
        except TenantMismatch as exc:
            raise ToolUnavailable("Connector scope mismatch") from exc
        env = await LolaBIService(gateway).invoke(self.bi_name, {k: v for k, v in args.items() if k != "entity_ids"})
        status = _BI_STATUS.get(str(env.get("status")), LolaStatus.UNAVAILABLE)
        evidence = []
        for src in env.get("sources") or []:
            if not isinstance(src, dict):
                continue
            fetched = src.get("fetched_at")
            try:
                fetched_at = datetime.fromisoformat(str(fetched).replace("Z", "+00:00")) if fetched else None
            except ValueError:
                fetched_at = None
            evidence.append(EvidenceRef(
                source_system=str(src.get("provider") or "quickbooks"), record_type=str(src.get("source") or "bi"),
                fetched_at=fetched_at or datetime.now(timezone.utc),
                is_stale=bool(src.get("stale")), note=str(src.get("quality") or "") or None,
            ))
        if status == LolaStatus.ANSWERED and not evidence:
            status = LolaStatus.PARTIAL
        return ToolOutput(data=env, summary=_finding_text(env)[:400], evidence=evidence, status=status,
                          warnings=[str(w) for w in (env.get("warnings") or [])][:5])


# Tools the planner can reach by intent (all read-only; see planning.INTENT_TOOLS).
BI_TOOLS: tuple[tuple[str, str], ...] = (
    ("finance_cash_position", "finance"),
    ("finance_profit_and_loss", "finance"),
    ("finance_cash_flow_projection", "finance"),
    ("ar_collection_priorities", "accounts_receivable"),
    ("ap_open_bills", "accounts_payable"),
    ("pricing_product_margins", "pricing"),
    ("sales_declining_accounts", "sales_crm"),
    ("investigate_account_decline", "cross_system"),
    ("bi_data_coverage", "cross_system"),
)


# ─── Workstream 3: logistics ─────────────────────────────────────────────────


def logistics_viewer_for(principal: Principal, authorizer: Authorizer):
    """Lola principal → WS3 ``LogisticsViewer`` (DB role, Lola account scope)."""
    from app.intelligence.wholesale_distribution.logistics.visibility import LogisticsViewer

    base = LogisticsViewer.from_user({"sub": principal.user_id, "company_id": principal.company_id,
                                      "role": principal.role})
    scope = authorizer.account_scope()
    base.can_view_commercial = principal.has_financial_access
    if base.tier == "sales":
        base.customer_ids = set(scope.visible_ids)
    return base


class LogisticsAssistantTool(LolaTool):
    name = "logistics.assistant"
    department = Department.OPERATIONS
    resource = ResourceType.INVENTORY
    description = "Workstream 3 logistics Q&A over the verified shipment ledger (role-scoped)."
    needs_adapter = False

    async def fetch(self, ctx: ToolContext, args: dict, decision) -> ToolOutput:
        from app.intelligence.wholesale_distribution.logistics.assistant import LogisticsAssistant
        from app.intelligence.wholesale_distribution.logistics.journey import ShipmentLedger
        from app.intelligence.wholesale_distribution.logistics.store import SupabaseEventStore
        from app.intelligence.wholesale_distribution.logistics.visibility import LogisticsAccessDenied

        viewer = logistics_viewer_for(ctx.principal, ctx.authorizer)
        try:
            viewer.require_access()
        except LogisticsAccessDenied as exc:
            raise ToolUnavailable("Your role has no access to logistics data") from exc
        try:
            ledger = ShipmentLedger.load(ctx.principal.company_id, SupabaseEventStore(ctx.db))
        except Exception as exc:  # noqa: BLE001
            raise ToolUnavailable("Logistics data temporarily unavailable") from exc
        answer = LogisticsAssistant(ledger, viewer).ask(str(args.get("question") or ""))
        out = answer.to_dict()
        evidence = [
            EvidenceRef(source_system="logistics_ledger", record_type="shipment",
                        record_id=str(link.get("shipment_id") or link.get("id") or "") or None)
            for link in (out.get("links") or []) if isinstance(link, dict)
        ] or ([EvidenceRef(source_system="logistics_ledger", record_type="shipment")]
              if out.get("intent") != "unknown" else [])
        warnings = [f"Withheld: {r}" if isinstance(r, str) else f"Withheld: {r.get('reason', r)}"
                    for r in (out.get("restricted") or [])][:5]
        return ToolOutput(data=out, summary=str(out.get("answer") or "")[:600], evidence=evidence,
                          status=LolaStatus.ANSWERED if evidence else LolaStatus.PARTIAL, warnings=warnings)


def register_workstream_tools(registry) -> None:
    for name, dept in BI_TOOLS:
        registry.register(BITool(name, dept))
    registry.register(LogisticsAssistantTool())
