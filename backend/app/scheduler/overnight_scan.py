"""Read-only overnight scan for every active company.

The task receives a UnifiedDataAdapter, NOT the source-specific
QuickBooksAdapter. Source failures must propagate to the Celery task, which
records failure rather than publishing a misleading "all clear" snapshot.
No provider writes or notifications are performed here.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.intelligence.account_health import score_account
from app.intelligence.inventory_monitor import evaluate_inventory


async def run_overnight_scan(company_id: str, adapter) -> dict:
    """Analyze current account and inventory health via the unified adapter."""
    results = {
        "company_id": company_id,
        "accounts_scanned": 0,
        "alerts_detected": 0,
        "alerts_queued": 0,  # legacy field: no delivery/queue is performed in this scan
        "inventory_alerts": [],
        "account_alerts": [],
    }

    # UnifiedDataAdapter exposes these methods. Source-specific get_customers,
    # get_invoices and get_inventory belong to QuickBooksAdapter instead.
    # Never convert connection exceptions into a successful empty scan.
    customers = await adapter.get_all_customers()
    invoices = await adapter.get_orders_last_n_days(365)
    inventory = await adapter.get_inventory_qb()

    orders_by_customer: dict[str, list[dict]] = {}
    for inv in invoices:
        orders_by_customer.setdefault(inv.customer_id, []).append(
            {"date": inv.date, "total": float(inv.total)}
        )

    for customer in customers:
        cust_orders = orders_by_customer.get(customer.qb_id, [])
        last_order_date = max((o["date"] for o in cust_orders), default=None)
        health = score_account(
            account_id=customer.id,
            account_name=customer.name,
            last_order_date=last_order_date,
            avg_order_cycle_days=None,
            order_history=cust_orders,
            current_balance=customer.balance,
        )

        results["accounts_scanned"] += 1
        if health.status in ("at_risk", "dormant"):
            results["account_alerts"].append(
                {
                    "account_id": customer.id,
                    "account_name": customer.name,
                    "status": health.status,
                    "flags": health.flags,
                }
            )
            results["alerts_detected"] += 1

    for item in inventory:
        weekly_rate = _estimate_sell_rate(item.qb_id, invoices)
        status = evaluate_inventory(
            item_id=item.id,
            product_name=item.name,
            warehouse_1_qty=int(item.quantity_on_hand),
            warehouse_2_qty=0,
            weekly_sell_rate=Decimal(str(weekly_rate)),
        )

        if status.status in ("critical", "out_of_stock"):
            results["inventory_alerts"].append(
                {
                    "item_id": item.id,
                    "product_name": item.name,
                    "status": status.status,
                    "message": status.message,
                }
            )
            results["alerts_detected"] += 1

    return results


def _estimate_sell_rate(qb_item_id: str, invoices) -> float:
    """Estimate weekly unit consumption over the trailing 90 days."""
    cutoff = date.today() - timedelta(days=90)
    total_qty = 0.0
    for inv in invoices:
        if inv.date < cutoff:
            continue
        for line in inv.line_items:
            if str(line.get("item_id", "")) == qb_item_id:
                total_qty += float(line.get("quantity", 0))
    return round(total_qty / 13, 2)  # 90 days is approximately 13 weeks
