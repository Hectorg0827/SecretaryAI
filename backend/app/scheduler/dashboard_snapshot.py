"""
Dashboard snapshot helper.

Computes the expensive QB-derived dashboard payload (account health, inventory
alerts, 30-day sales) and upserts it into report_snapshots so every employee
reads the same cached result instead of each triggering a fresh QB round-trip.

Called by:
  - tasks/morning_briefing.py  (nightly, after briefing is generated)
  - POST /api/dashboard/refresh  (on-demand, manager+ only)
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

log = logging.getLogger(__name__)


async def compute_dashboard_payload(adapter) -> dict:
    """
    Pull data from QB/warehouse, score accounts and evaluate inventory.
    Returns a dict that matches the shape of GET /api/dashboard/summary
    (minus pending_actions and unread_emails, which are always live).
    """
    from app.intelligence.account_health import score_account
    from app.intelligence.inventory_monitor import evaluate_inventory

    health_counts: Counter = Counter()
    inventory_alerts: list[dict] = []
    sales_30d = 0.0

    # ── Account health ────────────────────────────────────────────────────
    try:
        customers = await adapter.get_all_customers()
        invoices_yr = await adapter.get_orders_last_n_days(365)

        by_customer: dict[str, list] = {}
        for inv in invoices_yr:
            cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
            by_customer.setdefault(cid, []).append({
                "date": getattr(inv, "date", None) or inv.get("order_date"),
                "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
            })

        for customer in customers:
            cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
            orders = by_customer.get(cid, [])
            dates = [o["date"] for o in orders if o["date"]]
            last_order = max(dates, default=None)
            if isinstance(last_order, str):
                try:
                    from datetime import date as _date
                    last_order = _date.fromisoformat(str(last_order)[:10])
                except ValueError:
                    last_order = None

            health = score_account(
                account_id=cid,
                account_name=getattr(customer, "name", ""),
                last_order_date=last_order,
                avg_order_cycle_days=None,
                order_history=orders,
                current_balance=float(getattr(customer, "balance", 0)),
            )
            health_counts[health.status] += 1
    except Exception as exc:
        log.error("Dashboard snapshot: account health failed: %s", exc)

    # ── Inventory alerts ──────────────────────────────────────────────────
    try:
        inventory = await adapter.get_inventory_merged()
        for item in inventory:
            status = evaluate_inventory(
                item_id=item.get("qb_id", ""),
                product_name=item.get("product_name", ""),
                warehouse_1_qty=int(item.get("warehouse_qty", 0)),
                warehouse_2_qty=int(item.get("qb_qty", 0)),
                weekly_sell_rate=Decimal(str(item.get("weekly_sell_rate", 0))),
            )
            if status.status in ("critical", "low", "out_of_stock"):
                inventory_alerts.append({
                    "item_id":         item.get("qb_id", ""),
                    "product_name":    item.get("product_name", ""),
                    "total_qty":       item.get("total_qty", 0),
                    "weeks_remaining": status.weeks_remaining,
                    "stock_status":    status.status,
                    "needs_po":        True,
                })
    except Exception as exc:
        log.error("Dashboard snapshot: inventory eval failed: %s", exc)

    # ── 30-day sales ──────────────────────────────────────────────────────
    try:
        invoices_30 = await adapter.get_orders_last_n_days(30)
        sales_30d = sum(
            float(getattr(inv, "total", 0) or inv.get("total_amount", 0))
            for inv in invoices_30
        )
    except Exception as exc:
        log.error("Dashboard snapshot: sales total failed: %s", exc)

    return {
        "accounts": {
            "healthy": health_counts.get("healthy", 0),
            "slowing": health_counts.get("slowing", 0),
            "at_risk": health_counts.get("at_risk", 0),
            "dormant": health_counts.get("dormant", 0),
            "total":   sum(health_counts.values()),
        },
        "inventory_alerts": inventory_alerts,
        "sales_30d":        round(sales_30d, 2),
    }


def store_snapshot(db, company_id: str, report_type: str, payload: dict, generated_by: str = "scheduler") -> None:
    """Upsert a report snapshot row. One row per (company, report_type)."""
    try:
        db.table("report_snapshots").upsert(
            {
                "company_id":   company_id,
                "report_type":  report_type,
                "payload":      payload,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": generated_by,
            },
            on_conflict="company_id,report_type",
        ).execute()
    except Exception as exc:
        log.error("Failed to store %s snapshot for company %s: %s", report_type, company_id, exc)


def read_snapshot(db, company_id: str, report_type: str) -> dict | None:
    """Return the latest snapshot payload for (company, report_type), or None."""
    try:
        result = (
            db.table("report_snapshots")
            .select("payload,generated_at,generated_by")
            .eq("company_id", company_id)
            .eq("report_type", report_type)
            .maybe_single()
            .execute()
        )
        return result.data or None
    except Exception as exc:
        log.error("Failed to read %s snapshot for company %s: %s", report_type, company_id, exc)
        return None
