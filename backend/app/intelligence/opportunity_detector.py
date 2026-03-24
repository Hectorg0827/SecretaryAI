"""
Opportunity detector — scans QB data for actionable business signals.
Returns a list of FeedEvent dicts ready to insert into feed_events.
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

DORMANT_DAYS = 45        # account not ordered in N days → flag
OVERDUE_DAYS = 15        # invoice overdue by N days → flag
LOW_STOCK_WEEKS = 2      # < N weeks of stock → flag


def detect_opportunities(
    customers: list,
    invoices: list,
    inventory: list[dict],
) -> list[dict]:
    """
    Run all detectors and return feed event dicts.
    Each dict matches the feed_events table schema (minus id/company_id/created_at).
    """
    events: list[dict] = []
    events.extend(_detect_dormant_accounts(customers, invoices))
    events.extend(_detect_overdue_invoices(invoices))
    events.extend(_detect_low_stock_no_po(inventory))
    return events


def _detect_dormant_accounts(customers, invoices) -> list[dict]:
    from app.intelligence.account_health import score_account
    events = []
    today = date.today()
    cutoff = today - timedelta(days=DORMANT_DAYS)

    by_customer: dict[str, list] = {}
    for inv in invoices:
        cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
        by_customer.setdefault(cid, []).append({
            "date": getattr(inv, "date", None) or inv.get("order_date"),
            "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
        })

    for customer in customers:
        cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
        name = getattr(customer, "name", "") or customer.get("name", "")
        orders = by_customer.get(cid, [])
        dates = [o["date"] for o in orders if o["date"]]
        if not dates:
            continue
        last_order = max(dates)
        if isinstance(last_order, str):
            try:
                last_order = date.fromisoformat(str(last_order)[:10])
            except ValueError:
                continue
        if last_order < cutoff:
            days_ago = (today - last_order).days
            events.append({
                "event_type": "dormant_account",
                "title": f"{name} hasn't ordered in {days_ago} days",
                "body": f"Last order was {last_order.strftime('%b %d')}. Consider reaching out.",
                "priority": "high" if days_ago > 60 else "medium",
                "action_label": "Draft Outreach",
                "action_type": "draft_customer_email",
                "action_data": {"account_name": name, "customer_id": cid},
                "entity_id": cid,
                "entity_name": name,
            })
    return events


def _detect_overdue_invoices(invoices) -> list[dict]:
    events = []
    today = date.today()
    for inv in invoices:
        due = getattr(inv, "due_date", None)
        if due is None:
            continue
        if isinstance(due, str):
            try:
                due = date.fromisoformat(str(due)[:10])
            except ValueError:
                continue
        balance = float(getattr(inv, "balance", 0) or 0)
        if balance <= 0:
            continue
        overdue_days = (today - due).days
        if overdue_days >= OVERDUE_DAYS:
            name = getattr(inv, "customer_name", "") or inv.get("customer_name", "")
            inv_id = getattr(inv, "qb_id", "") or inv.get("qb_id", "")
            events.append({
                "event_type": "overdue_invoice",
                "title": f"{name} — invoice {overdue_days}d overdue",
                "body": f"Outstanding balance ${balance:,.2f}, due {due.strftime('%b %d')}.",
                "priority": "high" if overdue_days > 30 else "medium",
                "action_label": "Draft Reminder",
                "action_type": "draft_customer_email",
                "action_data": {"account_name": name, "invoice_id": inv_id, "balance": balance},
                "entity_id": inv_id,
                "entity_name": name,
            })
    return events[:10]  # cap to avoid flooding


def _detect_low_stock_no_po(inventory: list[dict]) -> list[dict]:
    from app.intelligence.inventory_monitor import evaluate_inventory
    events = []
    for item in inventory:
        sell_rate = Decimal(str(item.get("weekly_sell_rate", 0)))
        status = evaluate_inventory(
            item_id=item.get("qb_id", ""),
            product_name=item.get("product_name", ""),
            warehouse_1_qty=int(item.get("warehouse_qty", 0)),
            warehouse_2_qty=int(item.get("qb_qty", 0)),
            weekly_sell_rate=sell_rate,
        )
        if status.status in ("critical", "out_of_stock"):
            name = item.get("product_name", "")
            events.append({
                "event_type": "low_stock",
                "title": f"{name} — {status.status.replace('_', ' ')}",
                "body": f"~{status.weeks_remaining or 0:.1f} weeks of stock remaining. Draft a PO?",
                "priority": "high",
                "action_label": "Draft PO",
                "action_type": "draft_purchase_order",
                "action_data": {"item_id": item.get("qb_id", ""), "product_name": name},
                "entity_id": item.get("qb_id", ""),
                "entity_name": name,
            })
    return events
