"""Role-scoped growth alerts and opportunities from connected sales and stock.

Signals are derived from the tenant's QuickBooks invoices and merged
inventory, then classified with the opportunity taxonomy, which decides the
briefing section, the review step and which roles may see each signal. A
signal whose evidence is not verified keeps its section but shows no metric.
Analysis only: nothing is written, ordered or sent.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response
from app.api.insights._product_sales import (
    aggregate_sales, load_catalog, money, periods, product_vitals, read_sales,
)
from app.auth.rbac import require_permission
from app.intelligence.growth.opportunity_taxonomy import (
    SIGNAL_TAXONOMY, GrowthSignal, PolicyScope, classify_growth_signal,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/growth-opportunities"
TAG = "insights: growth opportunities"

PERMISSION = "run_reports"
SECTIONS = ("red_alerts", "yellow_alerts", "opportunities", "scoreboard")
# Signals are computed from a read made moments earlier in this request.
MAX_AGE_SECONDS = 900
# Invoices and item costs come straight from QuickBooks records that the
# connector normalizers validated; they are not independently reconciled.
QUICKBOOKS_CONFIDENCE = 80


@router.get("")
async def growth_opportunities(
    weeks: int = Query(4, ge=1, le=26),
    change_threshold_pct: int = Query(20, ge=5, le=100),
    min_margin_pct: int = Query(15, ge=0, le=90),
    user: dict = Depends(require_permission(PERMISSION)),
    adapter=Depends(get_adapter),
):
    """List the alerts and opportunities this role may review, by section."""
    inventory, invoices = await read_sales(adapter, days=weeks * 14)
    read_at = datetime.now(timezone.utc)
    catalog, catalog_skipped = load_catalog(inventory)
    current, previous = periods(date.today(), weeks, with_previous=True)
    window = aggregate_sales(invoices, catalog, current, previous)
    rows_skipped = catalog_skipped + window.rows_skipped
    company_id = user["company_id"]
    role = user.get("role", "")

    candidates: list[tuple[str, str, Decimal, str]] = []  # (item_id, code, metric, quality)
    for product in catalog.values():
        now = current.products.get(product.item_id)
        before = previous.products.get(product.item_id)
        if now is None and before is None:
            continue
        try:
            vitals = product_vitals(company_id, product, window, weeks)
        except ValueError:
            log.warning("Growth opportunity row skipped")
            rows_skipped += 1
            continue
        growth = vitals.net_case_growth_pct
        if growth is not None and growth >= change_threshold_pct:
            candidates.append((product.item_id, "brand_growth", growth, "verified"))
        elif growth is not None and growth <= -change_threshold_pct:
            candidates.append((product.item_id, "brand_decline", growth, "verified"))
        margin = vitals.gross_margin_pct
        if margin is not None and margin < min_margin_pct:
            candidates.append((product.item_id, "margin_breach", margin, "verified"))
        if product.stock is not None and product.stock == 0 and now is not None and now.net_cases > 0:
            # Quantity evidence carries the adapter's own reconciliation flag.
            candidates.append((product.item_id, "portfolio_stockout_with_demand",
                               now.net_cases, product.stock_quality))

    scope = PolicyScope(
        company_id=company_id,
        effective_role=role,
        permitted_entity_refs=frozenset(catalog),
        # Derived on the server from the authenticated role and permission.
        policy_decision_id=f"rbac:{PERMISSION}:{role}",
    )
    sections: dict[str, list[dict]] = {name: [] for name in SECTIONS}
    as_of = datetime.now(timezone.utc)
    for item_id, code, metric, quality in candidates:
        signal = GrowthSignal(
            company_id=company_id,
            entity_ref=item_id,
            signal_code=code,
            department=SIGNAL_TAXONOMY[code].department,
            metric_value=metric,
            source_quality=quality,
            source_record_id=f"quickbooks:item:{item_id}",
            source_revision=current.label(),
            observed_at=read_at,
            source_system="quickbooks",
            source_fetched_at=read_at,
            source_confidence=QUICKBOOKS_CONFIDENCE,
        )
        try:
            result = classify_growth_signal(signal, scope=scope, as_of=as_of, max_age_seconds=MAX_AGE_SECONDS)
        except PermissionError:
            continue  # Not visible to this role; omitted, not counted.
        except ValueError:
            log.warning("Growth opportunity signal rejected")
            rows_skipped += 1
            continue
        product = catalog[item_id]
        sections.setdefault(result.section, []).append({
            "signal_code": result.signal_code,
            "department": result.department,
            "entity_ref": result.entity_ref,
            "product_name": product.name,
            "sku": product.sku,
            "status": result.status,
            "display_metric": money(result.display_metric, 1),
            "next_action_code": result.next_action_code,
            "evidence_quality": result.evidence_quality,
            "source_system": result.source_system,
            "source_confidence": result.source_confidence,
            "approval_required": result.approval_required,
        })

    for entries in sections.values():
        entries.sort(key=lambda entry: (entry["signal_code"], entry["product_name"]))
    return advisory_response({
        "weeks": weeks,
        "current_period": current.as_dict(),
        "previous_period": previous.as_dict(),
        "thresholds": {"change_pct": change_threshold_pct, "min_margin_pct": min_margin_pct},
        "sections": sections,
        "signals_total": sum(len(entries) for entries in sections.values()),
        "rows_skipped": rows_skipped,
    }, source="connected_data")
