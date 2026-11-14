"""
Account health refresh — re-scores all accounts nightly.
Updates health_status and health_score in the DB.
"""
import logging
from datetime import date, timedelta
from app.connectors.unified_adapter import read_invoice_summaries
from app.scheduler.dashboard_snapshot import _billable_invoice_rows

log = logging.getLogger(__name__)


def _store_account_health(db, company_id: str, customer_id: str, payload: dict) -> None:
    """A matched tenant-scoped DB row is required before claiming an update."""
    if not hasattr(db, "table"):
        raise RuntimeError("Account health destination unavailable")
    result = (db.table("accounts").update(payload)
              .eq("qb_customer_id", customer_id).eq("company_id", company_id).execute())
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("Account health persistence was not confirmed")
    row = rows[0]
    if (not isinstance(row, dict) or str(row.get("company_id")) != str(company_id)
            or str(row.get("qb_customer_id")) != str(customer_id)):
        raise RuntimeError("Account health update did not match its company and customer")


async def refresh_all_account_health(company_id: str, adapter, db) -> dict:
    """Re-score every account and persist updated scores."""
    from app.intelligence.account_health import score_account

    results = {"updated": 0, "unscored": 0, "failed": 0, "alerts": []}

    customers = await adapter.get_all_customers()
    date_to = date.today()
    date_from = date_to - timedelta(days=365)
    invoices = _billable_invoice_rows(await read_invoice_summaries(adapter, 365))

    # Group invoices by customer
    by_customer: dict[str, list[dict]] = {}
    for inv in invoices:
        # _billable_invoice_rows produces verified dictionaries with "date"
        # and "total" keys. Do not read obsolete order_date/total_amount keys.
        cid = inv["customer_id"]
        by_customer.setdefault(cid, []).append({
            "date": inv["date"],
            "total": inv["total"],
        })

    for customer in customers:
        cid = customer.qb_id if hasattr(customer, "qb_id") else customer.get("qb_id", "")
        orders = by_customer.get(cid, [])
        last_order = max((o["date"] for o in orders), default=None)
        if isinstance(last_order, str):
            try:
                last_order = date.fromisoformat(last_order)
            except ValueError:
                last_order = None

        # A missing balance is unknown, not $0: score_account rejects it and
        # the account is marked unscored below.
        balance = customer.get("balance") if isinstance(customer, dict) else getattr(customer, "balance", None)

        try:
            health = score_account(
                account_id=cid,
                account_name=customer.name if hasattr(customer, "name") else customer.get("name", ""),
                last_order_date=last_order,
                avg_order_cycle_days=None,
                order_history=orders,
                current_balance=balance,
            )
        except ValueError:
            # A legitimate credit balance is negative; other unsupported
            # records are also unscored. Never turn them into at-risk scores
            # or abort the rest of the company's nightly account refresh.
            log.warning("Health refresh: account %s is unscored", cid)
            results["unscored"] += 1
            try:
                _store_account_health(db, company_id, cid, {
                    "health_status": "unknown",
                    "health_score": None,
                    "last_health_check": date.today().isoformat(),
                })
            except Exception as exc:
                log.error("Health refresh: unscored DB update failed for %s: %s", cid, type(exc).__name__)
                results["failed"] += 1
            continue

        # Update account in DB
        try:
            _store_account_health(db, company_id, cid, {
                "health_status": health.status,
                "health_score": health.score,
                "last_health_check": date.today().isoformat(),
            })
        except Exception as e:
            log.error("Health refresh: DB update failed for %s: %s", cid, type(e).__name__)
            results["failed"] += 1
            continue

        results["updated"] += 1

        if health.status in ("at_risk", "dormant"):
            results["alerts"].append({
                "account_id": cid,
                "account_name": health.account_name,
                "status": health.status,
                "score": health.score,
                "flags": health.flags,
            })

    log.info(
        "Account health refresh: %d accounts updated, %d alerts",
        results["updated"], len(results["alerts"]),
    )
    return results
