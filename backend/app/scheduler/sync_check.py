"""
Sync health check scheduler — runs every 4 hours.
Reconciles QB data vs warehouse/ordering system data.
Fires alerts if variance exceeds thresholds.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# sync_checks.status only allows healthy/warning/broken/unknown; an
# unmapped value makes the insert fail and the result is silently lost.
_DB_STATUS = {"healthy": "healthy", "warning": "warning", "critical": "broken", "error": "broken"}


async def run_sync_check(company_id: str, adapter, db) -> dict:
    """
    Pull data from all sources and reconcile.
    Returns a sync health summary.
    """
    from app.intelligence.sync_health import reconcile_inventory, summarize_sync_health

    results = {
        "company_id": company_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks_run": 0,
        "discrepancies": [],
        "status": "healthy",
    }

    # Get QB inventory
    try:
        qb_inventory = await adapter.get_inventory_qb()
        qb_dicts = [
            {
                "product_name": item.name,
                "qb_qty": float(item.quantity_on_hand),
            }
            for item in qb_inventory
        ]
        results["checks_run"] += 1
    except Exception:
        log.error("Sync check: QB inventory fetch failed")
        results["status"] = "error"
        _persist(db, company_id, results)
        return results

    # Get warehouse data (Computer Use if needed)
    try:
        merged = await adapter.get_inventory_merged()
        warehouse_dicts = [
            {
                "product_name": item["product_name"],
                "quantity": item.get("warehouse_qty", 0),
            }
            for item in merged
            if item.get("source") == "qb+warehouse"
        ]

        if warehouse_dicts:
            discrepancies = reconcile_inventory(qb_dicts, warehouse_dicts)
            summary = summarize_sync_health(discrepancies)
            results["discrepancies"] = summary["discrepancies"]
            results["status"] = summary["status"]
            results["checks_run"] += 1
    except Exception:
        log.warning("Sync check: warehouse reconciliation skipped")

    _persist(db, company_id, results)
    return results


def _persist(db, company_id: str, results: dict) -> None:
    """Store the result; failed checks are recorded too, so they stay visible."""
    try:
        record = {
            "company_id": company_id,
            "check_type": "inventory_reconciliation",
            "status": _DB_STATUS.get(results["status"], "unknown"),
            "details": results,
            "checked_at": results["checked_at"],
        }
        if hasattr(db, "table"):
            db.table("sync_checks").insert(record).execute()
    except Exception:
        log.error("Sync check: failed to write result to DB")
