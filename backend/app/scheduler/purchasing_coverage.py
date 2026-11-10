"""Tenant-scoped purchasing coverage for the staging-only executive digest.

Only aggregate database observations are exposed. This does not inspect
freight, customs, supplier claims, external inventory, or financial ledgers.
"""
from __future__ import annotations

from collections import Counter

PO_STATUSES = ("draft", "approved", "partially_received", "received")
MAX_ROWS = 500


class PurchasingCoverageInvalid(ValueError):
    """An observed purchasing payload cannot safely be represented as evidence."""


def _rows(query) -> list[dict]:
    result = query.limit(MAX_ROWS + 1).execute()
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise PurchasingCoverageInvalid("Purchasing evidence missing or exceeds bounded read")
    if any(not isinstance(row, dict) for row in rows):
        raise PurchasingCoverageInvalid("Purchasing evidence row malformed")
    return rows


def unavailable_purchasing_coverage() -> dict:
    """Unavailable is never misrepresented as an observed zero count."""
    return {
        "status": "unavailable",
        "source_quality": "unavailable",
        "reason": "purchasing_source_unavailable",
        "purchase_orders_by_status": None,
        "purchase_orders_total": None,
        "pending_receipts": None,
    }


def read_purchasing_coverage(db, company_id: str) -> dict:
    """Read only the approved tenant's PO and pending-receipt headers."""
    if not isinstance(company_id, str) or not company_id.strip():
        raise ValueError("Company scope required")
    try:
        orders = _rows(
            db.table("purchasing_orders")
            .select("id,status,company_id")
            .eq("company_id", company_id)
        )
        receipts = _rows(
            db.table("staged_warehouse_receipts")
            .select("id,company_id")
            .eq("company_id", company_id)
            .eq("status", "pending")
        )
        order_ids = [row.get("id") for row in orders]
        receipt_ids = [row.get("id") for row in receipts]
        if any(row.get("company_id") != company_id for row in orders + receipts):
            raise PurchasingCoverageInvalid("Cross-tenant purchasing data returned")
        if (any(not isinstance(value, str) or not value for value in order_ids + receipt_ids)
                or len(set(order_ids)) != len(order_ids)
                or len(set(receipt_ids)) != len(receipt_ids)):
            raise PurchasingCoverageInvalid("Purchasing source identity invalid")
        if any(row.get("status") not in PO_STATUSES for row in orders):
            raise PurchasingCoverageInvalid("Unrecognized purchasing order state")
        counts = Counter(row["status"] for row in orders)
        result = {
            "status": "observed",
            "source_quality": "partial",
            "reason": None,
            "purchase_orders_by_status": {status: counts[status] for status in PO_STATUSES},
            "purchase_orders_total": len(orders),
            "pending_receipts": len(receipts),
        }
        return validate_purchasing_coverage(result)
    except Exception:
        # SQL errors, incompletely observed reads and malformed rows all fail
        # closed. Provider/database exception text must not reach the digest.
        return unavailable_purchasing_coverage()


def validate_purchasing_coverage(value: dict) -> dict:
    """Fail closed on altered/stale cache shapes before executive projection."""
    if not isinstance(value, dict) or set(value) != {
        "status", "source_quality", "reason", "purchase_orders_by_status",
        "purchase_orders_total", "pending_receipts",
    }:
        raise PurchasingCoverageInvalid("Purchasing coverage schema invalid")
    if value["status"] == "unavailable":
        if value != unavailable_purchasing_coverage():
            raise PurchasingCoverageInvalid("Unavailable purchasing evidence is ambiguous")
        return value
    if (value["status"] != "observed" or value["source_quality"] != "partial"
            or value["reason"] is not None):
        raise PurchasingCoverageInvalid("Purchasing evidence quality not supported")
    counts = value["purchase_orders_by_status"]
    if not isinstance(counts, dict) or set(counts) != set(PO_STATUSES):
        raise PurchasingCoverageInvalid("Purchasing states incomplete")
    if any(type(n) is not int or n < 0 for n in counts.values()):
        raise PurchasingCoverageInvalid("Purchasing state counts invalid")
    total = value["purchase_orders_total"]
    pending = value["pending_receipts"]
    if (type(total) is not int or type(pending) is not int or total < 0 or pending < 0
            or sum(counts.values()) != total or total > MAX_ROWS or pending > MAX_ROWS):
        raise PurchasingCoverageInvalid("Purchasing aggregate inconsistent")
    return value
