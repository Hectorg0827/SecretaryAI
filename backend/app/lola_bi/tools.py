"""Lola BI tool registry and service — the stable interface for Workstream 1.

WS1's orchestrator should:
  1. resolve a ``BIAccessScope`` server-side (``BIScopeProvider``),
  2. build a ``BusinessDataGateway`` for that scope and tenant connector,
  3. offer ``LolaBIService.tool_definitions()`` to the model (already filtered
     to what the caller may use; names match ``^[a-z0-9_]+$``),
  4. execute model tool calls with ``await service.invoke(name, arguments)``.

Every invocation returns the ``lola.bi.v1`` envelope (see ``contracts.envelope``)
with ``status`` ok | partial | unavailable | denied | invalid_request. Tool
inputs are validated strictly; unknown fields are rejected. All tools are
read-only and never execute actions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.lola_bi import finance, investigate, payables, pricing, receivables, sales
from app.lola_bi.contracts import (
    BIAccessDenied, ResultBuilder, SourceUnavailable, TenantMismatch, envelope, utcnow,
)
from app.lola_bi.money import FxRate
from app.lola_bi.scope import Cap

log = logging.getLogger(__name__)

DEPARTMENTS = {
    "finance": "Franklin — Finance / CPA",
    "accounts_receivable": "Wildora — Accounts Receivable",
    "accounts_payable": "Accounts Payable",
    "pricing": "Raul — Pricing / Billbacks",
    "sales_crm": "Sales & CRM",
    "cross_system": "Lola — cross-system",
}

MAX_WINDOW_DAYS = 400


class InvalidToolInput(ValueError):
    """Caller-supplied arguments are invalid (distinct from bad source data)."""


# ─── parameter models ────────────────────────────────────────────────────────

class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoParams(_Params):
    pass


class PeriodParams(_Params):
    start: Optional[date] = Field(None, description="First day (YYYY-MM-DD). Default depends on tool.")
    end: Optional[date] = Field(None, description="Last day (YYYY-MM-DD). Defaults to today.")

    @model_validator(mode="after")
    def _order(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class PnlParams(PeriodParams):
    compare_prior: bool = Field(True, description="Also return the immediately preceding period of equal length.")


class CashFlowParams(_Params):
    weeks: int = Field(13, ge=1, le=13, description="Number of weeks to schedule (max 13).")


class OverdueParams(_Params):
    min_days_overdue: int = Field(1, ge=1, le=3650)
    limit: int = Field(25, ge=1, le=200)


class DueParams(_Params):
    within_days: int = Field(14, ge=0, le=120)


class LimitParams(_Params):
    limit: int = Field(15, ge=1, le=100)


class BillsParams(_Params):
    due_within_days: int = Field(14, ge=0, le=120)


class ProposalParams(_Params):
    horizon_days: int = Field(14, ge=0, le=60)
    cash_reserve: Decimal = Field(Decimal("0"), ge=0, max_digits=15, decimal_places=2,
                                  description="Cash to keep untouched, in home currency.")


class MarginParams(PeriodParams):
    top_n: int = Field(15, ge=1, le=100)


class DiscrepancyParams(PeriodParams):
    tolerance_pct: Decimal = Field(Decimal("10"), gt=0, le=100)


class AsOfParams(_Params):
    as_of: Optional[date] = Field(None, description="Reference day; defaults to today. Cannot be in the future.")


class TrendParams(AsOfParams):
    window_days: int = Field(90, ge=7, le=180, description="Length of each compared window.")


class DeclineParams(TrendParams):
    min_decline_pct: Decimal = Field(Decimal("20"), gt=0, le=100)
    limit: int = Field(25, ge=1, le=200)


class TerritoryParams(TrendParams):
    territory: str = Field(..., min_length=1, max_length=100)


class CustomerParams(_Params):
    customer: str = Field(..., min_length=1, max_length=200, description="Customer name, QuickBooks ID or email.")


class InvestigateParams(CustomerParams, TrendParams):
    pass


class EntityParams(_Params):
    query: str = Field(..., min_length=1, max_length=200)
    kind: Literal["customer", "product", "vendor"] = "customer"


# ─── registry ────────────────────────────────────────────────────────────────

Handler = Callable[["LolaBIService", Any, ResultBuilder, Any], Awaitable[dict]]


@dataclass(frozen=True)
class LolaTool:
    name: str
    department: str
    description: str
    capabilities: frozenset[str]
    params: type[_Params]
    handler: Handler

    def definition(self) -> dict:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name,
                "description": f"[{DEPARTMENTS[self.department]}] {self.description} Read-only.",
                "input_schema": schema}


def _period(svc: "LolaBIService", p: PeriodParams, default_days: int) -> tuple[date, date]:
    end = p.end or svc.today()
    start = p.start or (end - timedelta(days=default_days - 1))
    svc.check_dates(start, end)
    return start, end


def _as_of(svc: "LolaBIService", p: AsOfParams) -> date:
    day = p.as_of or svc.today()
    svc.check_dates(day, day)
    return day


async def _pnl(svc: "LolaBIService", gw: Any, rb: ResultBuilder, p: PnlParams) -> dict:
    end = p.end or svc.today()
    start = p.start or end.replace(day=1)
    svc.check_dates(start, end)
    return await finance.profit_and_loss(gw, rb, start=start, end=end, compare_prior=p.compare_prior)


TOOLS: list[LolaTool] = [
    LolaTool("finance_cash_position", "finance",
             "Current cash: QuickBooks book balance of each active bank account, totals per currency.",
             frozenset({Cap.FINANCE_CASH}), NoParams,
             lambda s, g, rb, p: finance.cash_position(g, rb, fx_rates=s.fx_rates)),
    LolaTool("finance_profit_and_loss", "finance",
             "Profit & Loss totals (income, COGS, gross profit, expenses, net income) for a period; "
             "default month to date. QuickBooks Online only.",
             frozenset({Cap.FINANCE_PNL}), PnlParams, _pnl),
    LolaTool("finance_cash_flow_projection", "finance",
             "Week-by-week cash schedule from bank balances, receivables and payables by due date "
             "(contractual schedule, not a forecast; payroll/taxes excluded).",
             frozenset({Cap.FINANCE_CASHFLOW}), CashFlowParams,
             lambda s, g, rb, p: finance.cash_flow_projection(g, rb, as_of=s.today(), weeks=p.weeks)),
    LolaTool("finance_profitability_by_brand", "finance",
             "Estimated gross profit and margin by brand from invoice lines and current item costs; default last 90 days.",
             frozenset({Cap.FINANCE_PROFITABILITY}), PeriodParams,
             lambda s, g, rb, p: finance.profitability_by_brand(g, rb, **dict(zip(("start", "end"), _period(s, p, 90))))),
    LolaTool("finance_anomalies", "finance",
             "Scan for financial anomalies: duplicate invoice numbers, duplicate bills, credit balances, "
             "negative bank balances, unusual invoice amounts.",
             frozenset({Cap.FINANCE_ANOMALIES}), NoParams,
             lambda s, g, rb, p: finance.financial_anomalies(g, rb, as_of=s.today())),
    LolaTool("ar_receivables_summary", "accounts_receivable",
             "How much customers owe: total open receivables and aging buckets (current, 1-30, 31-60, 61-90, 90+).",
             frozenset({Cap.AR_COMPANY, Cap.AR_OWN}), NoParams,
             lambda s, g, rb, p: receivables.receivables_summary(g, rb, as_of=s.today())),
    LolaTool("ar_overdue_customers", "accounts_receivable",
             "Customers with overdue invoices, oldest first, with overdue balances.",
             frozenset({Cap.AR_COMPANY, Cap.AR_OWN}), OverdueParams,
             lambda s, g, rb, p: receivables.overdue_customers(g, rb, as_of=s.today(),
                                                               min_days_overdue=p.min_days_overdue, limit=p.limit)),
    LolaTool("ar_upcoming_due_invoices", "accounts_receivable",
             "Open customer invoices coming due within the next N days.",
             frozenset({Cap.AR_COMPANY, Cap.AR_OWN}), DueParams,
             lambda s, g, rb, p: receivables.upcoming_due(g, rb, as_of=s.today(), within_days=p.within_days)),
    LolaTool("ar_collection_priorities", "accounts_receivable",
             "Ranked list of customers to collect from, with the reasons for each rank.",
             frozenset({Cap.AR_COMPANY, Cap.AR_OWN}), LimitParams,
             lambda s, g, rb, p: receivables.collection_priorities(g, rb, as_of=s.today(), limit=p.limit)),
    LolaTool("ar_payment_reconciliation", "accounts_receivable",
             "Check that customer payments are fully allocated and invoice balances equal total minus payments; "
             "default invoices from the last 60 days.",
             frozenset({Cap.AR_COMPANY}), PeriodParams,
             lambda s, g, rb, p: receivables.payment_reconciliation(
                 g, rb, **dict(zip(("start", "end"), _period(s, p, 60))), as_of=s.today())),
    LolaTool("ap_open_bills", "accounts_payable",
             "Open supplier bills: overdue, due soon, and totals per currency, with possible duplicates.",
             frozenset({Cap.AP_READ}), BillsParams,
             lambda s, g, rb, p: payables.open_bills(g, rb, as_of=s.today(), due_within_days=p.due_within_days)),
    LolaTool("ap_duplicate_bills", "accounts_payable",
             "Detect possible duplicate supplier bills among open bills.",
             frozenset({Cap.AP_READ}), NoParams,
             lambda s, g, rb, p: payables.duplicate_bills(g, rb)),
    LolaTool("ap_payment_proposal", "accounts_payable",
             "Propose which bills to pay within a horizon given cash on hand. Proposal only; "
             "payments require approval through the approval workflow.",
             frozenset({Cap.AP_PROPOSE}), ProposalParams,
             lambda s, g, rb, p: payables.payment_proposal(g, rb, as_of=s.today(), horizon_days=p.horizon_days,
                                                           cash_reserve=p.cash_reserve)),
    LolaTool("pricing_product_margins", "pricing",
             "Products ranked by estimated gross margin (highest and lowest); default last 90 days.",
             frozenset({Cap.PRICING_READ}), MarginParams,
             lambda s, g, rb, p: pricing.product_margins(g, rb, **dict(zip(("start", "end"), _period(s, p, 90))),
                                                         top_n=p.top_n)),
    LolaTool("pricing_price_discrepancies", "pricing",
             "Invoice lines priced below cost or far from the usual/list price; default last 90 days.",
             frozenset({Cap.PRICING_READ}), DiscrepancyParams,
             lambda s, g, rb, p: pricing.price_discrepancies(
                 g, rb, **dict(zip(("start", "end"), _period(s, p, 90))), tolerance_pct=p.tolerance_pct)),
    LolaTool("pricing_billbacks_and_promotions", "pricing",
             "Supplier billbacks and promotion profitability (reports what data is missing).",
             frozenset({Cap.PRICING_READ}), NoParams,
             lambda s, g, rb, p: pricing.billbacks_and_promotions(g, rb)),
    LolaTool("sales_period_comparison", "sales_crm",
             "Sales this month to date vs the same days last year and last month.",
             frozenset({Cap.SALES_COMPANY, Cap.SALES_OWN}), AsOfParams,
             lambda s, g, rb, p: sales.period_comparison(g, rb, as_of=_as_of(s, p))),
    LolaTool("sales_declining_accounts", "sales_crm",
             "Accounts/distributors whose purchases fell versus the prior window.",
             frozenset({Cap.SALES_COMPANY, Cap.SALES_OWN}), DeclineParams,
             lambda s, g, rb, p: sales.declining_accounts(g, rb, as_of=_as_of(s, p), window_days=p.window_days,
                                                          min_decline_pct=p.min_decline_pct, limit=p.limit)),
    LolaTool("sales_underperforming_products", "sales_crm",
             "Products with falling sales or no sales despite stock on hand.",
             frozenset({Cap.SALES_COMPANY, Cap.SALES_OWN}), DeclineParams,
             lambda s, g, rb, p: sales.underperforming_products(g, rb, as_of=_as_of(s, p), window_days=p.window_days,
                                                                min_decline_pct=p.min_decline_pct, limit=p.limit)),
    LolaTool("sales_rep_attention", "sales_crm",
             "Which sales representatives need attention (declining books, inactive or overdue accounts).",
             frozenset({Cap.SALES_TEAM}), TrendParams,
             lambda s, g, rb, p: sales.rep_attention(g, rb, as_of=_as_of(s, p), window_days=p.window_days)),
    LolaTool("sales_territory_drivers", "sales_crm",
             "What is driving a territory's sales change: by account and by product, with stock checks.",
             frozenset({Cap.SALES_TEAM, Cap.SALES_COMPANY}), TerritoryParams,
             lambda s, g, rb, p: sales.territory_drivers(g, rb, territory=p.territory, as_of=_as_of(s, p),
                                                         window_days=p.window_days)),
    LolaTool("sales_projection", "sales_crm",
             "Projected sales for the current month using a simple run-rate (uncalibrated).",
             frozenset({Cap.SALES_COMPANY, Cap.SALES_OWN}), AsOfParams,
             lambda s, g, rb, p: sales.sales_projection(g, rb, as_of=_as_of(s, p))),
    LolaTool("crm_customer_profile", "sales_crm",
             "One customer's picture: balances, overdue, sales trend, top products, territory, rep.",
             frozenset({Cap.CRM_READ}), CustomerParams,
             lambda s, g, rb, p: sales.customer_profile(g, rb, customer=p.customer, as_of=s.today())),
    LolaTool("crm_find_entity", "cross_system",
             "Find a customer, product or vendor by name, ID or email; reports ambiguity instead of guessing.",
             frozenset({Cap.CRM_READ, Cap.AP_READ}), EntityParams,
             lambda s, g, rb, p: sales.entity_lookup(g, rb, query=p.query, kind=p.kind)),
    LolaTool("investigate_account_decline", "cross_system",
             "Why is an account ordering less? Combines sales, product mix, stock, prices, balances and "
             "correspondence; separates verified facts from hypotheses.",
             frozenset({Cap.CRM_READ}), InvestigateParams,
             lambda s, g, rb, p: investigate.account_decline(g, rb, customer=p.customer, as_of=_as_of(s, p),
                                                             window_days=p.window_days)),
]

async def _coverage(svc: "LolaBIService", gw: Any, rb: ResultBuilder, p: NoParams) -> dict:
    """Probe each source the caller may read and report live/stale/missing/unverified."""
    today = svc.today()
    probes = [
        ("company_currency", gw.company_currency()), ("customers", gw.customers()),
        ("products", gw.products()), ("open_invoices", gw.open_invoices()),
        ("invoices_last_30_days", gw.invoices(today - timedelta(days=29), today)),
        ("payments_last_30_days", gw.payments(today - timedelta(days=29), today)),
        ("open_bills", gw.open_bills()), ("bank_accounts", gw.bank_accounts()),
        ("profit_and_loss_month_to_date", gw.profit_and_loss(today.replace(day=1), today)),
        ("inventory", gw.inventory()), ("account_directory", gw.account_directory()),
        ("mailbox_metadata", gw.messages(max_results=5)),
    ]
    sources = []
    for label, call in probes:
        snap = await call
        info = snap.describe(rb.now)
        if snap.status.value == "denied":
            state = "not_in_your_scope"
        elif not snap.usable:
            state = snap.status.value
        elif snap.is_stale(rb.now):
            state = "stale"
        elif snap.quality.value == "unverified_mapping":
            state = "live_unverified_mapping"
        elif snap.status.value == "partial":
            state = "live_partial"
        else:
            state = "live"
        sources.append({"name": label, "state": state, **{k: info[k] for k in
                                                          ("provider", "coverage", "fetched_at", "record_count", "error")}})
    not_connected = [
        "supplier billbacks and promotion terms", "credit memos and vendor credits",
        "sales orders / estimates (open order book)", "payroll and tax schedules",
        "bank-statement balances (only QuickBooks book balances are read)",
        "general ledger detail and historical AR snapshots", "FX rates for multi-currency totals",
    ]
    return rb.finish({"sources": sources, "not_connected": not_connected,
                      "rule": "missing or failed data is reported as unavailable, never as zero"})


TOOLS.append(LolaTool("bi_data_coverage", "cross_system",
                      "Which business data sources are live, stale, failing, partial, unverified or not connected.",
                      frozenset({Cap.COVERAGE_READ, Cap.FINANCE_CASH}), NoParams, _coverage))

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


# ─── service ─────────────────────────────────────────────────────────────────

class LolaBIService:
    def __init__(self, gateway: Any, *, timezone: str | None = None, fx_rates: Iterable[FxRate] = (),
                 clock: Callable[[], datetime] = utcnow):
        self._gw = gateway
        self._clock = clock
        try:
            self._tz = ZoneInfo(timezone) if timezone else ZoneInfo("UTC")
        except (ZoneInfoNotFoundError, ValueError):
            self._tz = ZoneInfo("UTC")
        self.fx_rates = tuple(fx_rates)

    @property
    def scope(self):
        return self._gw.scope

    def today(self) -> date:
        return self._clock().astimezone(self._tz).date()

    def check_dates(self, start: date, end: date) -> None:
        if end > self.today():
            raise InvalidToolInput("Dates cannot be in the future")
        if start > end:
            raise InvalidToolInput("start must be on or before end")
        if (end - start).days + 1 > MAX_WINDOW_DAYS:
            raise InvalidToolInput(f"Date range cannot exceed {MAX_WINDOW_DAYS} days")
        return None

    def allowed_tools(self) -> list[LolaTool]:
        return [t for t in TOOLS if self.scope.has(*t.capabilities)]

    def tool_definitions(self) -> list[dict]:
        return [t.definition() for t in self.allowed_tools()]

    async def invoke(self, name: str, arguments: dict | None = None) -> dict:
        tool = TOOLS_BY_NAME.get(name)
        department = tool.department if tool else "cross_system"
        rb = ResultBuilder(name, department, self.scope, now=self._clock())
        if tool is None:
            return self._plain(rb, "invalid_request", ["unknown tool"])
        if not self.scope.has(*tool.capabilities):
            return self._plain(rb, "denied", ["this request is outside your permitted business data"])
        try:
            params = tool.params.model_validate(arguments or {})
        except ValidationError as exc:
            msgs = [f"{'.'.join(str(x) for x in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()]
            return self._plain(rb, "invalid_request", msgs[:10])
        try:
            return await tool.handler(self, self._gw, rb, params)
        except SourceUnavailable as exc:
            return rb.unavailable(exc)
        except (BIAccessDenied, TenantMismatch):
            log.warning("Lola BI tool denied by scope")
            return self._plain(rb, "denied", ["this request is outside your permitted business data"])
        except InvalidToolInput as exc:
            return self._plain(rb, "invalid_request", [str(exc)])
        except ValueError:
            log.warning("Lola BI source data failed validation")
            rb.note_missing("source_data", "malformed_or_conflicting",
                            "records failed integrity checks (e.g. conflicting duplicates); no figures produced")
            return envelope(tool=rb.tool, department=rb.department, scope=self.scope, status="unavailable",
                            answer=None, sources=rb.sources(), missing=rb.missing, warnings=rb.warnings,
                            findings=[], now=rb.now)

    def _plain(self, rb: ResultBuilder, status: str, warnings: list[str]) -> dict:
        return envelope(tool=rb.tool, department=rb.department, scope=self.scope, status=status, answer=None,
                        sources=[], missing=[], warnings=warnings, findings=[], now=rb.now)
