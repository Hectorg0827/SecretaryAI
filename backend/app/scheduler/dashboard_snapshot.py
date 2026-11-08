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

# Snapshot scoring changed: unobserved 365-day history is UNKNOWN, not risk.
# A persisted v1 snapshot must be recomputed before it can be trusted.
DASHBOARD_SCORING_POLICY_VERSION = 2


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


def _billable_invoice_rows(invoices, *, customer_ids: set[str] | None = None) -> list[dict]:
    """Normalize observed invoice headers and exclude void/draft documents.

    The Accounts API and dashboard must count the same billed history.
    Identity duplication and malformed source values still fail closed.
    """
    from app.intelligence.sales_analytics import (
        _EXCLUDED_INVOICE_STATUSES,
        filter_billable_invoices,
    )

    rows = []
    for inv in invoices:
        customer_id = _row_value(inv, "customer_id")
        if customer_ids is not None and customer_id not in customer_ids:
            # An inactive or unrelated customer's history cannot affect the
            # health score of this verified active-customer set.
            continue
        status = _row_value(inv, "status")
        if str(status or "").strip().casefold() in _EXCLUDED_INVOICE_STATUSES:
            # A void/draft document is not billed history; malformed line
            # headers on it must not void a legitimate billed-only report.
            continue
        invoice_id = _row_value(inv, "qb_id") or _row_value(inv, "id")
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            raise ValueError("Dashboard invoice missing source identifier")
        rows.append({
            "id": invoice_id.strip(),
            "customer_id": customer_id,
            "customer_name": _row_value(inv, "customer_name", ""),
            "date": _invoice_date(inv),
            "total": _invoice_amount(inv),
            "status": status,
        })
    return filter_billable_invoices(rows)


async def compute_dashboard_payload(adapter) -> dict:
    """
    Pull data from QB/warehouse, score accounts and evaluate inventory.
    Returns a dict that matches the shape of GET /api/dashboard/summary
    (minus pending_actions and unread_emails, which are always live).
    """
    from app.intelligence.account_health import score_account
    from app.intelligence.inventory_monitor import evaluate_inventory
    from app.connectors.unified_adapter import read_invoice_summaries

    health_counts: Counter = Counter()
    inventory_alerts: list[dict] = []
    inventory_unassessed = 0
    sales_30d = 0.0

    # ── Account health ────────────────────────────────────────────────────
    try:
        customers = await adapter.get_all_customers()
        active_customer_ids = {
            _row_value(customer, "qb_id") or _row_value(customer, "external_id")
            for customer in customers
        }
        if None in active_customer_ids or "" in active_customer_ids:
            raise ValueError("Customer missing external identifier")
        invoices_yr = _billable_invoice_rows(
            await read_invoice_summaries(adapter, 365), customer_ids=active_customer_ids,
        )

        by_customer: dict[str, list] = {}
        for inv in invoices_yr:
            cid = inv["customer_id"]
            if not cid:
                raise ValueError("Invoice missing customer_id")
            by_customer.setdefault(cid, []).append({
                "date": inv["date"],
                "total": inv["total"],
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
                balance = Decimal(str(balance))
                if not balance.is_finite():
                    raise ValueError("Customer balance must be finite")
                # A 365-day window with no billed orders does not establish
                # customer dormancy or risk. Credit balances likewise need AR
                # reconciliation; these policies match the Accounts API.
                if not orders or balance < 0:
                    health_counts["unknown"] += 1
                    continue
                health = score_account(
                    account_id=cid,
                    account_name=_row_value(customer, "name", ""),
                    last_order_date=last_order,
                    avg_order_cycle_days=None,
                    order_history=orders,
                    current_balance=balance,
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
            if not qty.is_finite() or qty < 0:
                raise ValueError("Inventory quantity must be finite and nonnegative")
            # UnifiedDataAdapter may supply 0.0 as an API-compatibility
            # placeholder even if the inventory sync never observed velocity.
            # A missing measurement is NOT a measured zero: an outage of the
            # velocity source fails closed, but one item without a measured
            # rate (e.g. a new SKU) is unassessed rather than voiding the
            # whole snapshot. Zero on hand is observed, so it still alerts.
            if item.get("weekly_sell_rate_observed") is not True:
                if item.get("weekly_sell_rate_source_status") == "unavailable":
                    raise ValueError("Inventory weekly sell rate source unavailable")
                if qty == 0:
                    inventory_alerts.append({
                        "item_id":         item.get("qb_id", ""),
                        "product_name":    item.get("product_name", ""),
                        "total_qty":       item.get("total_qty", 0),
                        "weeks_remaining": None,
                        "stock_status":    "out_of_stock",
                        "needs_po":        True,
                    })
                else:
                    inventory_unassessed += 1
                continue
            rate_value = item.get("weekly_sell_rate")
            if rate_value is None:
                raise ValueError("Inventory missing weekly sell rate")
            rate = Decimal(str(rate_value))
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
        invoices_30 = _billable_invoice_rows(await read_invoice_summaries(adapter, 30))
        sales_30d = sum(
            inv["total"]
            for inv in invoices_30
        )
        if not isfinite(sales_30d):
            raise ValueError("Thirty-day revenue aggregate is not finite")
    except Exception as exc:
        log.error("Dashboard snapshot: sales total failed")
        raise SnapshotSourceUnavailable("sales_30d") from exc

    return {
        "scoring_policy_version": DASHBOARD_SCORING_POLICY_VERSION,
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
        # In-stock items whose sell rate was never measured: not assessed,
        # so absent from alerts, but never silently treated as healthy.
        "inventory_unassessed": inventory_unassessed,
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
    # Absent from legacy snapshots: coverage is unknown, not assessed at zero.
    unassessed = payload.get("inventory_unassessed")
    if "inventory_unassessed" in payload and (
        type(unassessed) is not int or unassessed < 0
    ):
        raise SnapshotDataInvalid("Dashboard inventory coverage invalid")
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
        # postgrest-py 0.16.x returns None (rather than a response with
        # data=None) when maybe_single() finds no matching row.
        if result is None:
            return None
        return result.data or None
    except Exception as exc:
        log.error("Failed to read dashboard snapshot")
        raise SnapshotReadUnavailable("Dashboard snapshot storage unavailable") from exc
