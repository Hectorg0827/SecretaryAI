"""
Lola tools — department capabilities wrapped behind one safe interface.

Every tool:
  * declares the department, the resource type and the data sensitivity it touches;
  * is authorised BEFORE any source is read (a denied tool never calls the source);
  * filters records AFTER retrieval (record level) and strips fields the caller
    may not see (attribute level);
  * returns a :class:`ToolResult` with explicit status, evidence and the
    :class:`PermissionDecision` that applied.

Other workstreams add capabilities by subclassing :class:`LolaTool`, implementing
``fetch`` and calling ``registry.register(MyTool())``. ``fetch`` receives a
:class:`ToolContext` and must only use ``ctx.authorizer`` to decide visibility —
never the raw role.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from app.lola.authorization import Authorizer, Principal
from app.lola.contracts import (
    Department,
    EvidenceRef,
    LolaStatus,
    PermissionDecision,
    ResourceType,
    Sensitivity,
    ToolResult,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_EVIDENCE = 25


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


@dataclass
class ToolContext:
    principal: Principal
    authorizer: Authorizer
    db: Any
    adapter: Any = None
    request_id: str = ""
    today: date = field(default_factory=date.today)


class ToolUnavailable(RuntimeError):
    """The data source behind a tool is not connected or not readable."""


@dataclass
class ToolOutput:
    """What ``fetch`` returns; the base class turns it into a ToolResult."""

    data: Any
    summary: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    status: LolaStatus = LolaStatus.ANSWERED
    warnings: list[str] = field(default_factory=list)


class LolaTool:
    name: str = ""
    department: Department = Department.GENERAL
    resource: ResourceType = ResourceType.CUSTOMER_ACCOUNT
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    description: str = ""
    needs_adapter: bool = True

    async def fetch(self, ctx: ToolContext, args: dict, decision: PermissionDecision) -> ToolOutput:
        raise NotImplementedError

    async def run(self, ctx: ToolContext, args: Optional[dict] = None) -> ToolResult:
        args = dict(args or {})
        started = utcnow()
        decision = ctx.authorizer.can(self.resource, "read")
        if not decision.allowed:
            return ToolResult(
                tool=self.name, department=self.department, status=LolaStatus.DENIED,
                summary=decision.reason, authorization=decision, sensitivity=self.sensitivity,
                started_at=started, finished_at=utcnow(), error_code="permission_denied",
            )
        if self.needs_adapter and ctx.adapter is None:
            return self._unavailable(decision, started, "source_not_connected",
                                     "This data source is not connected")
        try:
            out = await self.fetch(ctx, args, decision)
        except ToolUnavailable as exc:
            return self._unavailable(decision, started, "source_unavailable", str(exc) or "Source unavailable")
        except Exception:
            log.warning("Lola tool %s failed", self.name, exc_info=True)
            return self._unavailable(decision, started, "source_error",
                                     "The data source could not be read right now")
        return ToolResult(
            tool=self.name, department=self.department, status=out.status,
            summary=out.summary, data=out.data, evidence=out.evidence[:MAX_EVIDENCE],
            authorization=decision, sensitivity=self.sensitivity,
            started_at=started, finished_at=utcnow(), warnings=out.warnings,
        )

    def _unavailable(self, decision, started, code, summary) -> ToolResult:
        return ToolResult(
            tool=self.name, department=self.department, status=LolaStatus.UNAVAILABLE,
            summary=summary, authorization=decision, sensitivity=self.sensitivity,
            started_at=started, finished_at=utcnow(), error_code=code,
        )


# ─── Shared helpers for account-scoped tools ─────────────────────────────────


async def visible_customers(ctx: ToolContext) -> list[Any]:
    customers = await ctx.adapter.get_all_customers()
    return [c for c in customers if ctx.authorizer.can_see_account(_get(c, "qb_id"))]


def _filter_ids(items: list[Any], ids: list[str], key: str = "qb_id") -> list[Any]:
    if not ids:
        return items
    wanted = {str(i) for i in ids}
    return [i for i in items if str(_get(i, key)) in wanted]


# ─── Sales ───────────────────────────────────────────────────────────────────


class CustomerAccountsTool(LolaTool):
    name = "sales.customer_accounts"
    department = Department.SALES
    resource = ResourceType.CUSTOMER_ACCOUNT
    description = "Customer accounts the caller may see (assigned, team, territory or company)."

    async def fetch(self, ctx, args, decision):
        customers = _filter_ids(await visible_customers(ctx), args.get("entity_ids") or [])
        rows = []
        evidence = []
        for c in customers:
            record = {
                "qb_id": str(_get(c, "qb_id", "")),
                "name": _get(c, "name", ""),
                "state": _get(c, "state"),
                "balance": _num(_get(c, "balance")),
            }
            rows.append(ctx.authorizer.redact(record, decision))
            evidence.append(EvidenceRef(source_system="quickbooks", record_type="customer",
                                        record_id=record["qb_id"]))
        summary = f"{len(rows)} account(s) in your scope" if rows else "No accounts in your scope matched"
        return ToolOutput(data={"accounts": rows, "scope": decision.scope}, summary=summary, evidence=evidence)


class SalesPerformanceTool(LolaTool):
    name = "sales.performance"
    department = Department.SALES
    resource = ResourceType.SALES
    description = "Revenue and order count over the last 30 days, limited to visible accounts."

    async def fetch(self, ctx, args, decision):
        days = int(args.get("days") or 30)
        invoices = await ctx.adapter.get_orders_last_n_days(days)
        ids = set(str(i) for i in (args.get("entity_ids") or []))
        visible = [
            inv for inv in invoices
            if ctx.authorizer.can_see_account(_get(inv, "customer_id"))
            and (not ids or str(_get(inv, "customer_id")) in ids)
        ]
        total = Decimal(0)
        by_account: dict[str, Decimal] = {}
        unobserved = 0
        for inv in visible:
            amount = _num(_get(inv, "total"))
            if amount is None:
                unobserved += 1
                continue
            total += Decimal(str(amount))
            name = str(_get(inv, "customer_name") or _get(inv, "customer_id") or "unknown")
            by_account[name] = by_account.get(name, Decimal(0)) + Decimal(str(amount))
        top = sorted(by_account.items(), key=lambda kv: kv[1], reverse=True)[:5]
        warnings = [f"{unobserved} invoice(s) had no readable total and were excluded"] if unobserved else []
        evidence = [EvidenceRef(source_system="quickbooks", record_type="invoice",
                                record_id=str(_get(i, "qb_id") or _get(i, "id") or "")) for i in visible]
        return ToolOutput(
            data={
                "days": days, "scope": decision.scope, "order_count": len(visible),
                "total_revenue": float(total),
                "top_accounts": [{"name": n, "revenue": float(v)} for n, v in top],
            },
            summary=f"{len(visible)} order(s) totalling {float(total):,.2f} in the last {days} days ({decision.scope} scope)",
            evidence=evidence,
            status=LolaStatus.PARTIAL if unobserved else LolaStatus.ANSWERED,
            warnings=warnings,
        )


# ─── Finance ─────────────────────────────────────────────────────────────────


class ReceivablesTool(LolaTool):
    name = "finance.receivables"
    department = Department.FINANCE
    resource = ResourceType.RECEIVABLES
    sensitivity = Sensitivity.CONFIDENTIAL
    description = "Open and overdue invoice balances (confidential)."

    async def fetch(self, ctx, args, decision):
        invoices = await ctx.adapter.get_orders_last_n_days(int(args.get("days") or 90))
        ids = set(str(i) for i in (args.get("entity_ids") or []))
        company = decision.scope == "company"
        open_total = Decimal(0)
        overdue_total = Decimal(0)
        overdue = 0
        count = 0
        evidence = []
        for inv in invoices:
            cid = _get(inv, "customer_id")
            if not company and not ctx.authorizer.can_see_account(cid):
                continue
            if ids and str(cid) not in ids:
                continue
            bal = _num(_get(inv, "balance"))
            if not bal or bal <= 0:
                continue
            count += 1
            open_total += Decimal(str(bal))
            due = _as_date(_get(inv, "due_date"))
            if due and due < ctx.today:
                overdue += 1
                overdue_total += Decimal(str(bal))
            evidence.append(EvidenceRef(source_system="quickbooks", record_type="invoice",
                                        record_id=str(_get(inv, "qb_id") or _get(inv, "id") or "")))
        return ToolOutput(
            data={"scope": decision.scope, "open_invoices": count, "open_balance": float(open_total),
                  "overdue_invoices": overdue, "overdue_balance": float(overdue_total)},
            summary=f"{count} open invoice(s), {overdue} overdue",
            evidence=evidence,
        )


# ─── Operations ──────────────────────────────────────────────────────────────


class InventoryTool(LolaTool):
    name = "operations.inventory"
    department = Department.OPERATIONS
    resource = ResourceType.INVENTORY
    description = "Stock levels; purchase costs only for callers with financial access."

    async def fetch(self, ctx, args, decision):
        items = _filter_ids(await ctx.adapter.get_inventory_merged(), args.get("entity_ids") or [])
        rows, evidence, low = [], [], []
        for item in items:
            record = {
                "qb_id": str(_get(item, "qb_id", "")),
                "product_name": _get(item, "product_name", ""),
                "sku": _get(item, "sku"),
                "total_qty": _num(_get(item, "total_qty")),
                "reorder_point": _num(_get(item, "reorder_point")),
                "purchase_cost": _num(_get(item, "purchase_cost")),
                "reconciliation_required": bool(_get(item, "reconciliation_required", False)),
            }
            if (record["reorder_point"] is not None and record["total_qty"] is not None
                    and record["total_qty"] <= record["reorder_point"]):
                low.append(record["product_name"])
            rows.append(ctx.authorizer.redact(record, decision))
            evidence.append(EvidenceRef(
                source_system=str(_get(item, "source", "quickbooks")), record_type="inventory_item",
                record_id=record["qb_id"], is_stale=_get(item, "warehouse_source_status") == "stale",
            ))
        warnings = []
        if any(r.get("reconciliation_required") for r in rows):
            warnings.append("Some items differ between QuickBooks and the warehouse and need reconciliation")
        return ToolOutput(
            data={"items": rows, "low_stock": low},
            summary=f"{len(rows)} item(s); {len(low)} at or below reorder point",
            evidence=evidence, warnings=warnings,
        )


class PurchasingTool(LolaTool):
    name = "purchasing.open_orders"
    department = Department.PURCHASING
    resource = ResourceType.PURCHASING
    description = "Open purchase orders; totals only for callers with financial access."

    async def fetch(self, ctx, args, decision):
        pos = await ctx.adapter.get_purchase_orders()
        rows, evidence = [], []
        for po in pos:
            status = str(_get(po, "status", "") or "").lower()
            if status in ("closed", "received", "cancelled"):
                continue
            record = {
                "qb_id": str(_get(po, "qb_id", "") or _get(po, "id", "")),
                "vendor_name": _get(po, "vendor_name"),
                "status": status or "open",
                "expected_date": str(_get(po, "expected_date") or "") or None,
                "cost": _num(_get(po, "total")),
            }
            rows.append(ctx.authorizer.redact(record, decision))
            evidence.append(EvidenceRef(source_system="quickbooks", record_type="purchase_order",
                                        record_id=record["qb_id"]))
        return ToolOutput(data={"purchase_orders": rows}, summary=f"{len(rows)} open purchase order(s)",
                          evidence=evidence)


# ─── HR ──────────────────────────────────────────────────────────────────────


class HRDirectoryTool(LolaTool):
    name = "hr.directory"
    department = Department.HR
    resource = ResourceType.HR
    sensitivity = Sensitivity.RESTRICTED
    description = "Employee directory with roles and reporting lines (restricted)."
    needs_adapter = False

    async def fetch(self, ctx, args, decision):
        try:
            users = (ctx.db.table("users").select("id,full_name,role,is_active")
                     .eq("company_id", ctx.principal.company_id).execute()).data or []
        except Exception as exc:
            raise ToolUnavailable("Employee directory unavailable") from exc
        rows = [{"user_id": str(u.get("id")), "name": u.get("full_name"), "role": u.get("role"),
                 "active": u.get("is_active") is True} for u in users]
        evidence = [EvidenceRef(source_system="supabase.users", record_type="employee",
                                record_id=r["user_id"]) for r in rows]
        return ToolOutput(data={"employees": rows}, summary=f"{len(rows)} employee record(s)", evidence=evidence)


class PayrollTool(LolaTool):
    name = "hr.payroll"
    department = Department.HR
    resource = ResourceType.PAYROLL
    sensitivity = Sensitivity.RESTRICTED
    description = "Payroll. No payroll system is connected to Lion BI; always unavailable."
    needs_adapter = False

    async def fetch(self, ctx, args, decision):
        raise ToolUnavailable("No payroll system is connected to Lion BI")


# ─── Registry ────────────────────────────────────────────────────────────────


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, LolaTool] = {}

    def register(self, tool: LolaTool) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError(f"Tool name missing or duplicated: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[LolaTool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict]:
        return [
            {"name": t.name, "department": t.department.value, "resource": t.resource.value,
             "sensitivity": t.sensitivity.value, "description": t.description}
            for t in self._tools.values()
        ]


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in (CustomerAccountsTool(), SalesPerformanceTool(), ReceivablesTool(),
                 InventoryTool(), PurchasingTool(), HRDirectoryTool(), PayrollTool()):
        reg.register(tool)
    # Workstreams 2 (business intelligence) and 3 (logistics), run through Lola.
    from app.lola.integrations import register_workstream_tools
    register_workstream_tools(reg)
    return reg
