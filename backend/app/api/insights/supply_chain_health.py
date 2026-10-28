"""Supply chain health: SCOR metrics and the bottleneck (constraint) view (§22).

GET measures what the connected data supports: days of inventory (inventory
value against the last 12 months' cost of goods sold), days customers take to
pay, and observed supplier lead times from the purchasing ledger. Metrics
that need figures the systems don't hold (accounts payable, warehousing and
freight costs, process backlogs) are listed instead of shown as zero, and the
measured figures are offered to prefill the POST /preview form, which runs the
full SCOR + Theory of Constraints analysis on what the user confirms.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_adapter, get_db
from app.api.insights._common import advisory_response, read_source, row_value
from app.api.insights._supply_chain_sources import (
    ItemIndex, clean_invoices, finite_number, inventory_list, read_purchasing_history,
)
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.scor_toc import (
    ProcessNode, SCORDomain, SCORToCEngine,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/supply-chain-health"
TAG = "Insights: supply chain health"

WINDOW_DAYS = 365
# Below this share of sales revenue matched to a purchase cost, cost of goods
# sold would be understated and inventory days overstated, so it isn't shown.
MIN_COGS_COVERAGE = 0.8

NEEDS_INPUT = [
    {"metric": "days_payable_outstanding", "label": "Days to pay suppliers",
     "why": "Needs your accounts payable balance, which isn't read from your accounting connection."},
    {"metric": "cash_to_cash_cycle_days", "label": "Cash-to-cash cycle",
     "why": "Needs days to pay suppliers."},
    {"metric": "supply_chain_cost_pct_revenue", "label": "Supply chain cost share",
     "why": "Needs your warehousing, freight and handling costs."},
    {"metric": "perfect_order_fulfillment_pct", "label": "On-time, complete deliveries",
     "why": "Customer delivery dates aren't recorded in the connected systems."},
    {"metric": "primary_constraint", "label": "Bottleneck",
     "why": "Needs the backlog and workload of each step (receiving, picking, shipping, ...)."},
]


def _rounded(value, digits=1):
    return round(value, digits) if value is not None else None


@router.get("")
async def supply_chain_health(
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """Measured SCOR asset/responsiveness metrics plus prefill for the full check."""
    company_id = user["company_id"]
    inventory = inventory_list(
        await read_source(adapter.get_inventory_merged(), source_name="Inventory data")
    )
    raw_invoices = await read_source(
        adapter.get_orders_last_n_days(WINDOW_DAYS), source_name="Sales history",
    )
    customers = await read_source(adapter.get_all_customers(), source_name="Customer balances")
    purchasing = await read_source(
        read_purchasing_history(db, company_id), source_name="Purchasing records",
    )
    try:
        invoices, invoices_skipped = clean_invoices(raw_invoices, today=date.today(), days=WINDOW_DAYS)
        if not isinstance(customers, list):
            raise ValueError("Customer source returned no list")
    except ValueError as exc:
        log.warning("Supply chain health source malformed")
        raise HTTPException(status_code=503, detail="Business data is unavailable; no analysis was produced") from exc

    rows_skipped = invoices_skipped + purchasing.rows_skipped
    inventory_value = 0.0
    items_valued = items_missing_cost = 0
    costs: dict[int, float] = {}
    for index, item in enumerate(inventory):
        qty = finite_number(item.get("total_qty")) if isinstance(item, dict) else None
        if qty is None or qty < 0:
            rows_skipped += 1
            continue
        cost = finite_number(item.get("purchase_cost"))
        if cost is None or cost <= 0:
            items_missing_cost += 1
            continue
        costs[index] = cost
        inventory_value += qty * cost
        items_valued += 1

    revenue = sum(invoice.total for invoice in invoices)
    index = ItemIndex(inventory)
    cogs = line_revenue = costed_revenue = 0.0
    for invoice in invoices:
        for line in invoice.lines:
            qty = finite_number(line.get("quantity")) if isinstance(line, dict) else None
            amount = finite_number(line.get("amount")) if isinstance(line, dict) else None
            if qty is None or amount is None:
                rows_skipped += 1
                continue
            if qty <= 0 or amount <= 0:
                continue
            line_revenue += amount
            item = index.match(line)
            if item in costs:
                cogs += qty * costs[item]
                costed_revenue += amount
    coverage = costed_revenue / line_revenue if line_revenue > 0 else 0.0
    cogs_supported = coverage >= MIN_COGS_COVERAGE and cogs > 0

    ar_balance = 0.0
    for customer in customers:
        balance = finite_number(row_value(customer, "balance"))
        if balance is None:
            rows_skipped += 1
            continue
        ar_balance += balance
    ar_supported = revenue > 0 and ar_balance >= 0

    # AP and supply chain costs are unknown here; the metrics derived from
    # them are dropped below rather than reported from placeholder zeros.
    metrics = SCORToCEngine(company_id=company_id).compute_metrics(
        orders=[],
        inventory_value=inventory_value,
        cogs=cogs if cogs_supported else 0.0,
        revenue=max(revenue, 0.0),
        ar_balance=ar_balance if ar_supported else 0.0,
        ap_balance=0.0,
        supply_chain_costs=0.0,
        supplier_lead_times=purchasing.supplier_lead_times,
    )
    lead_obs = metrics.supplier_lead_time_observations
    return advisory_response({
        "company_id": company_id,
        "measured": {
            "days_inventory_outstanding": _rounded(metrics.days_inventory_outstanding) if cogs_supported else None,
            "days_sales_outstanding": _rounded(metrics.days_sales_outstanding) if ar_supported else None,
            "supplier_lead_time_days": _rounded(metrics.supplier_lead_time_days) if lead_obs else None,
            "supplier_lead_time_observations": lead_obs,
        },
        "needs_input": NEEDS_INPUT,
        "prefill": {
            "revenue": round(revenue, 2) if revenue > 0 else None,
            "cogs": round(cogs, 2) if cogs_supported else None,
            "inventory_value": round(inventory_value, 2) if items_valued else None,
            "ar_balance": round(ar_balance, 2) if ar_supported else None,
            "supplier_lead_times_days": [round(v, 1) for v in purchasing.supplier_lead_times],
        },
        "coverage": {
            "cogs_revenue_coverage_pct": round(coverage * 100, 1),
            "inventory_items_valued": items_valued,
            "inventory_items_missing_cost": items_missing_cost,
            "invoices_used": len(invoices),
            "customers_read": len(customers),
        },
        "summary": {"rows_skipped": rows_skipped},
        "window_days": WINDOW_DAYS,
    }, source="connected_data")


# ── Full SCOR + bottleneck check on figures the user confirms ─────────────────

Money = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]


class ProcessStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    domain: Literal["plan", "source", "make", "deliver", "return", "enable"] = "deliver"
    throughput_per_day: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    backlog: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    utilization_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    avg_cycle_time_hours: float = Field(ge=0, le=10_000, allow_inf_nan=False)


class SupplyChainPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revenue: Money
    cogs: Money
    inventory_value: Money
    ar_balance: Money
    ap_balance: Money
    supply_chain_costs: Money
    supplier_lead_times_days: list[Annotated[float, Field(gt=0, le=730, allow_inf_nan=False)]] = Field(
        default_factory=list, max_length=200,
    )
    process_steps: list[ProcessStep] = Field(default_factory=list, max_length=20)


@router.post("/preview")
async def supply_chain_health_preview(
    body: SupplyChainPreview,
    user: dict = Depends(require_permission("view_financials")),
):
    """SCOR metrics, benchmarks and ranked bottlenecks from entered figures."""
    nodes = [
        ProcessNode(
            node_id=f"step-{position}", name=step.name, domain=SCORDomain(step.domain),
            throughput_per_day=step.throughput_per_day, queue_depth=step.backlog,
            utilization_pct=step.utilization_pct, avg_cycle_time_hours=step.avg_cycle_time_hours,
        )
        for position, step in enumerate(body.process_steps, start=1)
    ]
    try:
        report = SCORToCEngine(company_id=user["company_id"]).full_analysis(
            orders=[],
            inventory_value=body.inventory_value,
            cogs=body.cogs,
            revenue=body.revenue,
            ar_balance=body.ar_balance,
            ap_balance=body.ap_balance,
            supply_chain_costs=body.supply_chain_costs,
            supplier_lead_times=list(body.supplier_lead_times_days),
            process_nodes=nodes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="These figures can't be used for a supply chain check") from exc
    return advisory_response({
        "company_id": user["company_id"],
        **report.to_dict(),
        "not_measured": ["Perfect order fulfillment and order cycle time need customer delivery records."],
    }, source="user_supplied")
