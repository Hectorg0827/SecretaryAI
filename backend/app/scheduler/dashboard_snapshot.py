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
from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite

log = logging.getLogger(__name__)


class SnapshotPersistenceUnavailable(RuntimeError):
    """A computed snapshot could not be stored durably."""


class SnapshotReadUnavailable(RuntimeError):
    """The snapshot store failed; distinguish outage from a missing cache row."""


class SnapshotDataInvalid(ValueError):
    """A stored dashboard payload does not meet the trusted read contract."""


class SnapshotSourceUnavailable(RuntimeError):
    """A required source read failed; do not publish fictitious zero metrics."""

    def __init__(self, source: str):
        self.source = source
        super().__init__(f"Required dashboard source unavailable: {source}")


def _row_value(row, field: str, default=None):
    """Read canonical dicts or QB adapter dataclasses; preserve valid zero values."""
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)


def _invoice_amount(row) -> float:
    """Reject missing, non-finite or otherwise untrustworthy invoice values."""
    value = _row_value(row, "total")
    if value is None:
        value = _row_value(row, "total_amount")
    if value is None or isinstance(value, bool):
        raise ValueError("Invoice missing or invalid total")
    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError("Invoice amount must be finite")
        amount = float(decimal_value)
        if not Decimal(str(amount)).is_finite():
            raise ValueError("Invoice amount exceeds supported range")
        return amount
    except (ValueError, ArithmeticError) as exc:
        raise ValueError("Invoice amount is not a finite number") from exc


def _invoice_date(row) -> date:
    """Require an actual source date; unknown is not 'customer never ordered'."""
    value = _row_value(row, "date")
    if value is None:
        value = _row_value(row, "order_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError("Invoice has invalid date") from exc
    raise ValueError("Invoice missing transaction date")


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
            cid = _row_value(inv, "customer_id")
            if not cid:
                raise ValueError("Invoice missing customer_id")
            by_customer.setdefault(cid, []).append({
                "date": _invoice_date(inv),
                "total": _invoice_amount(inv),
            })

        for customer in customers:
            cid = _row_value(customer, "qb_id") or _row_value(customer, "external_id")
            if not cid:
                raise ValueError("Customer missing external identifier")
            orders = by_customer.get(cid, [])
            dates = [o["date"] for o in orders if o["date"]]
            last_order = max(dates, default=None)

            try:
                balance = _row_value(customer, "balance")
                if balance is None or isinstance(balance, bool):
                    raise ValueError("Customer balance was not observed")
                health = score_account(
                    account_id=cid,
                    account_name=_row_value(customer, "name", ""),
                    last_order_date=last_order,
                    avg_order_cycle_days=None,
                    order_history=orders,
                    current_balance=Decimal(str(balance)),
                )
            except (ValueError, ArithmeticError):
                # One unscorable account (unobserved, malformed or credit
                # balance) must not void the others; a missing balance is
                # not $0, so never count it as healthy.
                log.warning("Dashboard snapshot: unscored account")
                health_counts["unknown"] += 1
                continue
            health_counts[health.status] += 1
    except Exception as exc:
        log.error("Dashboard snapshot: account health failed")
        raise SnapshotSourceUnavailable("account_health") from exc

    # ── Inventory alerts ──────────────────────────────────────────────────
    try:
        inventory = await adapter.get_inventory_merged()
        for item in inventory:
            if not isinstance(item, dict):
                raise ValueError("Merged inventory item must be a normalized mapping")
            # Chat 2's adapter resolves the authoritative quantity: QB and
            # warehouse counts are usually two views of the same stock.
            # Summing them would make a shortage disappear from the dashboard.
            authoritative_qty = item.get("total_qty")
            if authoritative_qty is None:
                raise ValueError("Inventory missing authoritative total_qty")
            qty = Decimal(str(authoritative_qty))
            # UnifiedDataAdapter may supply 0.0 as an API-compatibility
            # placeholder even if the inventory sync never observed velocity.
            # A missing measurement is NOT a measured zero.
            if item.get("weekly_sell_rate_observed") is not True:
                raise ValueError("Inventory weekly sell rate not observed")
            rate_value = item.get("weekly_sell_rate")
            if rate_value is None:
                raise ValueError("Inventory missing weekly sell rate")
            rate = Decimal(str(rate_value))
            if not qty.is_finite() or qty < 0:
                raise ValueError("Inventory quantity must be finite and nonnegative")
            if not rate.is_finite() or rate < 0:
                raise ValueError("Weekly sell rate must be finite and nonnegative")
            status = evaluate_inventory(
                item_id=item.get("qb_id", ""),
                product_name=item.get("product_name", ""),
                warehouse_1_qty=qty,  # Decimal preserves fractional item/weight units
                warehouse_2_qty=0,
                weekly_sell_rate=rate,
            )
            if status.status in ("critical", "low", "out_of_stock"):
                inventory_alerts.append({
                    "item_id":         item.get("qb_id", ""),
                    "product_name":    item.get("product_name", ""),
                    "total_qty":       item.get("total_qty", 0),
                    "weeks_remaining": float(status.weeks_remaining) if status.weeks_remaining is not None else None,
                    "stock_status":    status.status,
                    "needs_po":        True,
                })
    except Exception as exc:
        log.error("Dashboard snapshot: inventory eval failed")
        raise SnapshotSourceUnavailable("inventory") from exc

    # ── 30-day sales ──────────────────────────────────────────────────────
    try:
        invoices_30 = await adapter.get_orders_last_n_days(30)
        sales_30d = sum(
            _invoice_amount(inv)
            for inv in invoices_30
        )
        if not isfinite(sales_30d):
            raise ValueError("Thirty-day revenue aggregate is not finite")
    except Exception as exc:
        log.error("Dashboard snapshot: sales total failed")
        raise SnapshotSourceUnavailable("sales_30d") from exc

    return {
        "source_quality": "partial",  # underlying provider lineage not yet verified
        "source_read_status": "completed",  # required top-level reads succeeded
        "accounts": {
            "healthy": health_counts.get("healthy", 0),
            "slowing": health_counts.get("slowing", 0),
            "at_risk": health_counts.get("at_risk", 0),
            "dormant": health_counts.get("dormant", 0),
            "unknown": health_counts.get("unknown", 0),
            "total":   sum(health_counts.values()),
        },
        "inventory_alerts": inventory_alerts,
        "sales_30d":        round(sales_30d, 2),
    }


def store_snapshot(db, company_id: str, report_type: str, payload: dict, generated_by: str = "scheduler") -> None:
    """Upsert a report snapshot row. One row per (company, report_type)."""
    try:
        written = db.table("report_snapshots").upsert(
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
        log.error("Failed to store dashboard snapshot")
        raise SnapshotPersistenceUnavailable("Dashboard snapshot persistence failed") from exc

    # PostgREST's representation response is evidence that a row was accepted.
    # An empty/incorrect acknowledgement does not authorize an API/worker to
    # report a successful refresh. Do not retry a potentially uncertain write.
    rows = getattr(written, "data", None)
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or rows[0].get("company_id") != company_id
        or rows[0].get("report_type") != report_type
    ):
        raise SnapshotPersistenceUnavailable(
            "Dashboard snapshot persistence not confirmed"
        )


def snapshot_freshness(
    generated_at: str | datetime | None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 86400,
) -> dict:
    """Explicit age of a cached business snapshot; invalid timestamps are unknown.

    An old snapshot can still be displayed as historical data, but must never
    be confused with current source-backed operational intelligence.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if generated_at is None:
        return {"status": "unknown", "age_seconds": None, "stale": True}
    try:
        generated = (
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if isinstance(generated_at, str) else generated_at
        )
        if not isinstance(generated, datetime) or generated.tzinfo is None or generated.utcoffset() is None:
            raise ValueError("Invalid generated_at timezone")
        seconds = (current - generated).total_seconds()
        if seconds < -300:
            raise ValueError("Snapshot timestamp is implausibly in the future")
        age_seconds = round(max(0.0, seconds), 1)
        stale = age_seconds > max_age_seconds
        return {
            "status": "stale" if stale else "fresh",
            "age_seconds": age_seconds,
            "stale": stale,
        }
    except (TypeError, ValueError, OverflowError):
        return {"status": "unknown", "age_seconds": None, "stale": True}


def validate_dashboard_cached_payload(payload: dict) -> dict:
    """Validate data provenance and aggregates before exposing cached KPIs.

    A completed connector read is not a verified provider audit, but it must
    at least yield internally consistent, finite business metrics.
    Invalid cached data must never be represented as healthy zero activity.
    """
    if not isinstance(payload, dict):
        raise SnapshotDataInvalid("Dashboard cache payload missing")
    if payload.get("source_read_status") != "completed":
        raise SnapshotDataInvalid("Dashboard cache source read incomplete")
    # V1 snapshots lack independent provider lineage certification. A DB row
    # advertising verified cannot self-certify this business intelligence.
    if payload.get("source_quality") != "partial":
        raise SnapshotDataInvalid("Dashboard cache source quality unverified")

    accounts = payload.get("accounts")
    if not isinstance(accounts, dict):
        raise SnapshotDataInvalid("Dashboard account aggregates absent")
    keys = ("healthy", "slowing", "at_risk", "dormant", "total")
    if any(type(accounts.get(k)) is not int or accounts[k] < 0 for k in keys):
        raise SnapshotDataInvalid("Dashboard account aggregates invalid")
    # Unscored accounts are part of the total; snapshots written before the
    # unknown bucket existed never produced any.
    unknown = accounts.get("unknown", 0)
    if type(unknown) is not int or unknown < 0:
        raise SnapshotDataInvalid("Dashboard account aggregates invalid")
    if sum(accounts[k] for k in keys[:-1]) + unknown != accounts["total"]:
        raise SnapshotDataInvalid("Dashboard account aggregates inconsistent")

    revenue = payload.get("sales_30d")
    if type(revenue) not in (int, float):
        raise SnapshotDataInvalid("Dashboard revenue unavailable or invalid")
    try:
        finite_revenue = isfinite(float(revenue))
    except (ValueError, OverflowError, TypeError):
        finite_revenue = False
    if not finite_revenue:
        raise SnapshotDataInvalid("Dashboard revenue unavailable or invalid")

    alerts = payload.get("inventory_alerts")
    if not isinstance(alerts, list) or any(
        not isinstance(item, dict)
        or item.get("stock_status") not in ("critical", "low", "out_of_stock")
        for item in alerts
    ):
        raise SnapshotDataInvalid("Dashboard inventory alerts invalid")
    return payload


def read_snapshot(db, company_id: str, report_type: str) -> dict | None:
    """Return the cached snapshot or None only when the row is absent.

    Database read failures must never trigger a fresh source sync as if no
    cache existed. A separate authorized refresh path may be used instead.
    """
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
        log.error("Failed to read dashboard snapshot")
        raise SnapshotReadUnavailable("Dashboard snapshot storage unavailable") from exc
