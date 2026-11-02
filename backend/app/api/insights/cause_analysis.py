"""Why did it move? Ranked likely causes for sales drops and stock risks (§18).

Reads the tenant's last 12 months of invoices, current inventory and the
SecretaryAI purchasing ledger, finds customers who have gone quiet or are
spending much less, and items close to running out, then asks the causal
reasoning engine for ranked hypotheses. Hypotheses are heuristic, not proven
causes; returns and competitor activity are not in the connected data and are
listed as evidence gaps. Nothing is sent to customers or suppliers.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_adapter, get_db
from app.api.insights._common import advisory_response, read_source
from app.api.insights._supply_chain_sources import (
    CleanInvoice, ItemIndex, clean_invoices, finite_number, inventory_list,
    line_unit_price, norm, read_purchasing_history, sales_by_item,
)
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.causal_reasoning import CausalReasoningEngine

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/cause-analysis"
TAG = "Insights: cause analysis"

HISTORY_DAYS = 365
RECENT_DAYS = 90            # "this quarter" vs the 90 days before it
DEMAND_DAYS = 84            # average daily sales window for stock risks
SPIKE_DAYS = 14
MIN_ORDERS_FOR_CADENCE = 3
QUIET_RATIO = 1.5           # silent for 1.5x the usual gap between orders
MIN_QUIET_DAYS = 14
SPEND_DROP_PCT = 40.0
MIN_STOCK_COVER_DAYS = 14
MAX_RESULTS = 20

EVIDENCE_GAPS = [
    "Returns and credit notes are not read from your accounting connection, so quality problems may be missed.",
    "Competitor activity is not tracked, so price pressure from competitors may be missed.",
]


def _customer_drops(invoices: list[CleanInvoice], engine: CausalReasoningEngine, today: date) -> tuple[list[dict], dict]:
    by_customer: dict[str, list[CleanInvoice]] = {}
    no_customer = 0
    for invoice in invoices:
        key = invoice.customer_id or norm(invoice.customer_name)
        if key:
            by_customer.setdefault(key, []).append(invoice)
        else:
            no_customer += 1

    flagged: list[dict] = []
    too_little_history = 0
    recent_start = today - timedelta(days=RECENT_DAYS)
    prior_start = today - timedelta(days=2 * RECENT_DAYS)
    for customer_id, rows in by_customer.items():
        # One order per invoice day, so same-day invoices don't shrink the cadence.
        per_day: dict[date, dict] = {}
        for invoice in rows:
            entry = per_day.setdefault(invoice.day, {"amount": 0.0, "units": 0.0})
            entry["amount"] += invoice.total
            for line in invoice.lines:
                qty = finite_number(line.get("quantity")) if isinstance(line, dict) else None
                if qty is not None and qty > 0:
                    entry["units"] += qty
        days = sorted(per_day)
        if len(days) < MIN_ORDERS_FOR_CADENCE:
            too_little_history += 1
            continue
        usual_gap = statistics.mean((b - a).days for a, b in zip(days, days[1:]))
        days_since = (today - days[-1]).days
        recent = sum(per_day[d]["amount"] for d in days if d > recent_start)
        prior = sum(per_day[d]["amount"] for d in days if prior_start < d <= recent_start)
        drop_pct = (prior - recent) / prior * 100 if prior > 0 else 0.0

        trigger = None
        if days_since >= MIN_QUIET_DAYS and days_since > QUIET_RATIO * usual_gap:
            trigger = f"No order for {days_since} days (usually every {usual_gap:.0f} days)"
        elif drop_pct >= SPEND_DROP_PCT:
            trigger = f"Spent {drop_pct:.0f}% less in the last {RECENT_DAYS} days than the {RECENT_DAYS} days before"
        if trigger is None:
            continue

        name = rows[0].customer_name or customer_id
        analysis = engine.analyse_customer_drop(
            customer_id=customer_id,
            customer_name=name,
            order_history=[
                {"date": d.isoformat(), "amount": round(per_day[d]["amount"], 2), "units": per_day[d]["units"]}
                for d in days
            ],
            recent_returns=[],
            pricing_changes=_price_changes(rows),
            competitor_signals=[],
            days_since_last_order=days_since,
        )
        flagged.append({
            "customer_id": customer_id,
            "customer_name": name,
            "trigger": trigger,
            "days_since_last_order": days_since,
            "usual_days_between_orders": round(usual_gap, 1),
            "orders_in_window": len(days),
            "revenue_last_90_days": round(recent, 2),
            "revenue_prior_90_days": round(prior, 2),
            "analysis": analysis.to_dict(),
        })
    flagged.sort(key=lambda c: c["revenue_prior_90_days"], reverse=True)
    return flagged[:MAX_RESULTS], {
        "customers_reviewed": len(by_customer),
        "customers_flagged": len(flagged),
        "customers_with_too_little_history": too_little_history,
        "invoices_without_customer": no_customer,
    }


def _price_changes(rows: list[CleanInvoice]) -> list[dict]:
    """First vs latest price this customer paid per item, from invoice lines."""
    seen: dict[str, list[tuple[date, float, str]]] = {}
    for invoice in rows:
        for line in invoice.lines:
            if not isinstance(line, dict):
                continue
            qty = finite_number(line.get("quantity"))
            price = line_unit_price(line, qty) if qty is not None and qty > 0 else None
            key = norm(line.get("item_id")) or norm(line.get("name"))
            if price is None or not key:
                continue
            label = str(line.get("name") or line.get("item_id"))
            seen.setdefault(key, []).append((invoice.day, price, label))
    changes = []
    for points in seen.values():
        points.sort(key=lambda p: p[0])
        first, last = points[0], points[-1]
        if last[0] > first[0] and abs(last[1] - first[1]) / first[1] > 0.005:
            changes.append({
                "date": last[0].isoformat(), "sku": last[2],
                "old_price": round(first[1], 4), "new_price": round(last[1], 4),
            })
    return changes


def _stock_risks(inventory: list, invoices: list[CleanInvoice], purchasing, engine: CausalReasoningEngine,
                 today: date) -> tuple[list[dict], dict]:
    recent_invoices = [i for i in invoices if (today - i.day).days < DEMAND_DAYS]
    sales, skipped = sales_by_item(recent_invoices, ItemIndex(inventory))
    flagged: list[dict] = []
    for index, item in enumerate(inventory):
        if not isinstance(item, dict) or index not in sales:
            continue
        stock = finite_number(item.get("total_qty"))
        if stock is None:
            skipped += 1
            continue
        lines = sales[index]
        avg_daily = sum(line.quantity for line in lines) / DEMAND_DAYS
        sku_key = norm(item.get("sku"))
        lead_times = purchasing.lead_times_by_sku.get(sku_key, []) if sku_key else []
        typical_lead = statistics.mean(lead_times) if lead_times else None
        cover_days = max(stock, 0) / avg_daily
        threshold = max(MIN_STOCK_COVER_DAYS, typical_lead or 0)
        if cover_days >= threshold:
            continue

        recent_units = sum(line.quantity for line in lines if (today - line.day).days < SPIKE_DAYS)
        earlier_units = sum(line.quantity for line in lines if (today - line.day).days >= SPIKE_DAYS)
        earlier_rate = earlier_units / (DEMAND_DAYS - SPIKE_DAYS)
        spike = recent_units / SPIKE_DAYS >= 1.5 * earlier_rate and recent_units > 0 and earlier_rate > 0
        delays = []
        if typical_lead is not None:
            for age in purchasing.open_order_ages_by_sku.get(sku_key, []):
                if age - typical_lead > 2:
                    delays.append({"delay_days": round(age - typical_lead)})
        name = item.get("product_name") or item.get("sku") or ""
        analysis = engine.analyse_inventory_anomaly(
            sku=str(item.get("sku") or item.get("qb_id") or ""),
            sku_name=name,
            current_stock=int(stock) if stock.is_integer() and stock >= 0 else max(stock, 0.0),
            avg_daily_demand=avg_daily,
            pending_po_units=round(purchasing.open_units_by_sku.get(sku_key, 0.0)) if sku_key else 0,
            supplier_lead_time_days=typical_lead or 0.0,
            recent_demand_spike=spike,
            recent_supplier_delays=delays,
        )
        flagged.append({
            "item_id": item.get("qb_id"),
            "product_name": name,
            "sku": item.get("sku"),
            "on_hand": stock,
            "days_of_stock_left": round(cover_days, 1),
            "avg_daily_sales": round(avg_daily, 2),
            "typical_supplier_days": round(typical_lead, 1) if typical_lead is not None else None,
            "recent_sales_spike": spike,
            "late_purchase_orders": len(delays),
            "analysis": analysis.to_dict(),
        })
    flagged.sort(key=lambda s: s["days_of_stock_left"])
    return flagged[:MAX_RESULTS], {
        "items_with_recent_sales": len(sales),
        "items_flagged": len(flagged),
        "rows_skipped": skipped,
    }


@router.get("")
async def cause_analysis(
    user: dict = Depends(require_permission("view_customers")),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """Customers buying less and items running short, with ranked likely causes."""
    company_id = user["company_id"]
    raw_invoices = await read_source(
        adapter.get_orders_last_n_days(HISTORY_DAYS), source_name="Sales history",
    )
    inventory = inventory_list(
        await read_source(adapter.get_inventory_merged(), source_name="Inventory data")
    )
    purchasing = await read_source(
        read_purchasing_history(db, company_id), source_name="Purchasing records",
    )
    today = date.today()
    try:
        invoices, invoices_skipped = clean_invoices(raw_invoices, today=today, days=HISTORY_DAYS)
    except ValueError as exc:
        log.warning("Cause analysis sales source malformed")
        raise HTTPException(status_code=503, detail="Sales history is unavailable; no analysis was produced") from exc

    engine = CausalReasoningEngine(company_id=company_id)
    customers, customer_summary = _customer_drops(invoices, engine, today)
    stock, stock_summary = _stock_risks(inventory, invoices, purchasing, engine, today)
    rows_skipped = (invoices_skipped + customer_summary.pop("invoices_without_customer")
                    + purchasing.rows_skipped + stock_summary.pop("rows_skipped"))
    return advisory_response({
        "company_id": company_id,
        "customer_drops": customers,
        "stock_risks": stock,
        "summary": {**customer_summary, **stock_summary, "rows_skipped": rows_skipped},
        "evidence_gaps": EVIDENCE_GAPS,
        "window_days": HISTORY_DAYS,
        "interpretation": "Ranked hypotheses from rules, not proven causes. Check before acting.",
    }, source="connected_data")
