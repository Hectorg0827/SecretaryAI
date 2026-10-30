"""Portfolio review: which products to keep, fix or review for phase-out.

Each product's contribution margin, weeks of supply and working capital are
computed from QuickBooks invoices, item costs and the merged inventory, then
assessed with the portfolio rationalization rules. Stock age and product
dependencies are not held by connected systems, so those checks are not
applied here. Analysis only: no product is discontinued or changed.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response
from app.api.insights._product_sales import (
    ProductPeriod, aggregate_sales, load_catalog, money, periods, read_sales,
)
from app.auth.rbac import require_permission
from app.intelligence.portfolio.rationalization import (
    PortfolioPolicy, PortfolioSKU, assess_portfolio,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/portfolio-review"
TAG = "insights: portfolio review"

ZERO = Decimal("0")
DECISIONS = {
    "maintain_or_invest_candidate": "keep",
    "supply_review_required": "fix",
    "dependency_review_required": "fix",
    "rationalization_candidate_for_review": "review_phase_out",
    "data_review_required": "check_data",
}


def _row(product, totals: ProductPeriod, *, status: str, reasons, margin=None,
         weeks_of_supply=None, working_capital=None) -> dict:
    return {
        "item_id": product.item_id,
        "name": product.name,
        "sku": product.sku,
        "decision": DECISIONS[status],
        "status": status,
        "reasons": list(reasons),
        "net_sales": money(totals.net_sales),
        "net_cases": money(totals.net_cases),
        "contribution_margin_pct": money(margin, 1),
        "weeks_of_supply": money(weeks_of_supply, 1),
        "working_capital_tied": money(working_capital),
        "no_sales_in_period": totals.cases_sold == ZERO,
    }


@router.get("")
async def portfolio_review(
    weeks: int = Query(13, ge=4, le=52),
    min_margin_pct: int = Query(15, ge=-100, le=100),
    max_weeks_of_supply: int = Query(26, ge=1, le=520),
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
):
    """Assess every stocked or selling product over the last ``weeks``."""
    inventory, invoices = await read_sales(adapter, days=weeks * 7)
    catalog, rows_skipped = load_catalog(inventory)
    current, _ = periods(date.today(), weeks, with_previous=False)
    window = aggregate_sales(invoices, catalog, current, None)
    rows_skipped += window.rows_skipped
    # No connected stock-age source: the aged-stock threshold is never reached
    # and the response says the check was not applied.
    policy = PortfolioPolicy(Decimal(min_margin_pct), Decimal(max_weeks_of_supply), max_stock_age_days=0)

    products = []
    inactive = 0
    for product in catalog.values():
        totals = current.products.get(product.item_id, ProductPeriod())
        if product.item_id not in current.products and not product.stock:
            inactive += 1
            continue
        missing = []
        if product.stock is None:
            missing.append("missing_stock_reading")
        if product.unit_cost is None:
            missing.append("missing_product_cost")
        if missing:
            products.append(_row(product, totals, status="data_review_required", reasons=missing))
            continue
        net_cases = totals.net_cases
        sku = PortfolioSKU(
            company_id=user["company_id"],
            sku=product.item_id,
            net_sales=totals.net_sales,
            total_contribution=totals.net_sales - net_cases * product.unit_cost,
            stock_on_hand_cases=product.stock,
            weekly_case_velocity=net_cases / weeks if net_cases > 0 else ZERO,
            stock_age_days=0,
            landed_cost_per_case=product.unit_cost,
            source_record_id=f"quickbooks:item:{product.item_id}",
            source_revision=current.label(),
        )
        try:
            assessment = assess_portfolio(user["company_id"], (sku,), policy)[0]
        except ValueError:
            # e.g. returns exceeding sales leave negative net sales.
            log.warning("Portfolio review row skipped")
            rows_skipped += 1
            continue
        products.append(_row(
            product, totals, status=assessment.status, reasons=assessment.reasons,
            margin=assessment.contribution_margin_pct, weeks_of_supply=assessment.weeks_of_supply,
            working_capital=assessment.working_capital_tied,
        ))

    order = {"review_phase_out": 0, "fix": 1, "check_data": 2, "keep": 3}
    products.sort(key=lambda row: (order[row["decision"]], -(row["working_capital_tied"] or 0), row["name"]))
    summary = {decision: 0 for decision in order}
    for row in products:
        summary[row["decision"]] += 1
    phase_out_capital = sum(
        (Decimal(str(row["working_capital_tied"])) for row in products
         if row["decision"] == "review_phase_out" and row["working_capital_tied"] is not None),
        ZERO,
    )
    return advisory_response({
        "weeks": weeks,
        "period": current.as_dict(),
        "policy": {"min_margin_pct": min_margin_pct, "max_weeks_of_supply": max_weeks_of_supply},
        "summary": summary,
        "working_capital_in_phase_out_review": money(phase_out_capital),
        "products": products,
        "products_without_activity": inactive,
        "rows_skipped": rows_skipped,
        "checks_not_applied": ["aged_inventory", "product_dependencies"],
    }, source="connected_data")
