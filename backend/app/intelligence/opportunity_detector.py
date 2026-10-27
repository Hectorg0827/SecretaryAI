"""
Opportunity detector — scans QB data for actionable business signals.
Returns a list of FeedEvent dicts ready to insert into feed_events.
"""
from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

DORMANT_DAYS = 45        # account not ordered in N days → flag
OVERDUE_DAYS = 15        # invoice overdue by N days → flag
LOW_STOCK_WEEKS = 2      # < N weeks of stock → flag


def _field(record, name: str, default=None):
    """Read a normalized connector dataclass or mapping, preserving falsey values."""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _date(value, *, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        if value is None:
            raise ValueError("date is missing")
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Proactive insight source has missing or invalid {field_name}") from exc


def _money(value, *, field_name: str) -> Decimal:
    try:
        if value is None:
            raise ValueError("money is missing")
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Proactive insight source has invalid {field_name}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Proactive insight source has invalid {field_name}")
    return amount


def _nonnegative_measure(value) -> Decimal | None:
    """Unobserved or malformed quantities must never become a numeric zero."""
    try:
        measure = Decimal(str(value)) if value is not None else None
    except (ValueError, InvalidOperation, TypeError):
        return None
    return measure if measure is not None and measure.is_finite() and measure >= 0 else None


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
    events = []
    today = date.today()
    cutoff = today - timedelta(days=DORMANT_DAYS)

    by_customer: dict[str, list] = {}
    for inv in invoices:
        cid = _field(inv, "customer_id")
        if not cid:
            raise ValueError("Proactive insight invoice has missing customer_id")
        order_date = _field(inv, "date") or _field(inv, "order_date")
        by_customer.setdefault(cid, []).append(_date(order_date, field_name="order_date"))

    for customer in customers:
        cid = _field(customer, "qb_id")
        name = _field(customer, "name")
        if not cid or not name:
            raise ValueError("Proactive insight customer has missing identity")
        dates = by_customer.get(cid, [])
        if not dates:
            continue
        last_order = max(dates)
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
        due = _field(inv, "due_date")
        if due is None:
            continue
        due = _date(due, field_name="due_date")
        balance = _money(_field(inv, "balance"), field_name="balance")
        if balance <= 0:
            continue
        overdue_days = (today - due).days
        if overdue_days >= OVERDUE_DAYS:
            name = _field(inv, "customer_name")
            inv_id = _field(inv, "qb_id")
            if not name or not inv_id:
                raise ValueError("Overdue invoice has missing source identity")
            events.append({
                "event_type": "overdue_invoice",
                "title": f"{name} — invoice {overdue_days}d overdue",
                "body": f"Outstanding balance ${balance:,.2f}, due {due.strftime('%b %d')}.",
                "priority": "high" if overdue_days > 30 else "medium",
                "action_label": "Draft Reminder",
                "action_type": "draft_customer_email",
                "action_data": {"account_name": name, "invoice_id": inv_id, "balance": float(balance)},
                "entity_id": inv_id,
                "entity_name": name,
            })
    return events[:10]  # cap to avoid flooding


def _detect_low_stock_no_po(inventory: list[dict]) -> list[dict]:
    from app.intelligence.inventory_monitor import evaluate_inventory
    events = []
    for item in inventory:
        # This feed produces *purchase-order* suggestions. Never imply an
        # open PO is absent, or interpret missing demand/quantity as zero.
        if (not item.get("weekly_sell_rate_observed") or
                item.get("weekly_sell_rate_source_status") != "observed" or
                not item.get("open_po_qty_observed") or
                item.get("open_po_source_status") != "observed"):
            continue
        quantity_source = item.get("quantity_source")
        total_qty = _nonnegative_measure(item.get("total_qty"))
        sell_rate = _nonnegative_measure(item.get("weekly_sell_rate"))
        open_po_qty = _nonnegative_measure(item.get("open_po_qty"))
        if (quantity_source not in ("quickbooks", "warehouse") or
                total_qty is None or sell_rate is None or sell_rate == 0 or
                open_po_qty is None or open_po_qty != 0):
            continue
        expected_qty = _nonnegative_measure(
            item.get("qb_qty" if quantity_source == "quickbooks" else "warehouse_qty")
        )
        if (expected_qty != total_qty or total_qty != int(total_qty) or
                (quantity_source == "warehouse" and (
                    not item.get("warehouse_qty_observed") or
                    item.get("warehouse_source_status") != "observed"
                ))):
            continue
        if not item.get("qb_id") or not item.get("product_name"):
            continue
        status = evaluate_inventory(
            item_id=item.get("qb_id", ""),
            product_name=item.get("product_name", ""),
            # These are two observations of the same stock, not two warehouses.
            warehouse_1_qty=int(total_qty),
            warehouse_2_qty=0,
            weekly_sell_rate=sell_rate,
        )
        if status.needs_po and status.status in ("critical", "out_of_stock"):
            name = item.get("product_name", "")
            events.append({
                "event_type": "low_stock",
                "title": f"{name} — {status.status.replace('_', ' ')}",
                "body": f"~{status.weeks_remaining:.1f} weeks of stock remaining. Draft a PO?",
                "priority": "high",
                "action_label": "Draft PO",
                "action_type": "draft_purchase_order",
                "action_data": {"item_id": item.get("qb_id", ""), "product_name": name},
                "entity_id": item.get("qb_id", ""),
                "entity_name": name,
            })
    return events
