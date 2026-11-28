"""Deterministic AR collections prioritization on top of aged invoice snapshots.

Pure, tenant-scoped, exact-Decimal calculation — zero floating-point arithmetic,
zero external database/provider calls, and fail-closed on unavailable aging data.
Disputed invoices are tracked separately and excluded from automated dunning.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Mapping

_SEVERE_BUCKETS = frozenset({"days_61_90", "days_91_plus"})
_OVERDUE_BUCKETS = frozenset({"days_1_30", "days_31_60", "days_61_90", "days_91_plus"})
_PRIORITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "disputed_hold": 3,
    "monitor": 4,
}


def _parse_non_negative_decimal(value: object, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} must be a non-negative number")
    return parsed


def _parse_date(value: object, label: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{label} must be a date")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _resolve_promise_status(
    entry: Mapping[str, object] | None,
    as_of: date,
) -> tuple[str, str | None]:
    if not entry:
        return "none", None
    if not isinstance(entry, Mapping):
        raise ValueError("Promise-to-pay entry must be a mapping")
    raw_status = str(entry.get("status") or "open").strip().lower()
    if raw_status not in {"open", "kept", "broken"}:
        raise ValueError("Promise-to-pay status is invalid")
    due_raw = entry.get("due_date")
    due_iso: str | None = None
    due_dt: date | None = None
    if due_raw is not None:
        due_dt = _parse_date(due_raw, "promise_to_pay.due_date")
        due_iso = due_dt.isoformat()
    if raw_status == "broken":
        return "broken", due_iso
    if raw_status == "kept":
        return "kept", due_iso
    if due_dt is not None and due_dt < as_of:
        return "broken", due_iso
    return "active", due_iso


def prioritize_ar_collections(
    aging_report: Mapping[str, object],
    *,
    tenant_id: str,
    as_of: date,
    customer_names: Mapping[str, str] | None = None,
    credit_limits: Mapping[str, object] | None = None,
    disputed_invoice_ids: Iterable[str] | None = None,
    promises_to_pay: Mapping[str, Mapping[str, object]] | None = None,
    assigned_owners: Mapping[str, str] | None = None,
) -> dict:
    """Rank customer AR balances deterministically for human-approved collections."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise PermissionError("Tenant context required for AR collections prioritization")
    if not isinstance(aging_report, Mapping):
        raise ValueError("aging_report must be a mapping")
    if aging_report.get("tenant_id") != tenant_id:
        raise PermissionError("Aging report tenant is not authorized for this prioritization")
    if isinstance(as_of, datetime) or not isinstance(as_of, date):
        raise ValueError("as_of must be a business date")

    parsed_limits: dict[str, Decimal] = {}
    if credit_limits is not None:
        if not isinstance(credit_limits, Mapping):
            raise ValueError("credit_limits must be a mapping")
        for cid, raw_limit in credit_limits.items():
            if not isinstance(cid, str) or not cid.strip():
                raise ValueError("credit_limits keys must be non-empty customer IDs")
            parsed_limits[cid] = _parse_non_negative_decimal(
                raw_limit, f"credit_limit[{cid}]"
            )

    disputed_set: set[str] = set()
    if disputed_invoice_ids is not None:
        for inv_id in disputed_invoice_ids:
            if not isinstance(inv_id, str) or not inv_id.strip():
                raise ValueError("disputed_invoice_ids entries must be non-empty strings")
            disputed_set.add(inv_id)

    names = dict(customer_names or {})
    promises = dict(promises_to_pay or {})
    owners = dict(assigned_owners or {})
    exceptions = list(aging_report.get("exceptions") or [])

    if aging_report.get("status") == "unavailable" or aging_report.get("buckets") is None:
        return {
            "status": "unavailable",
            "tenant_id": tenant_id,
            "as_of": as_of.isoformat(),
            "source_as_of": aging_report.get("source_as_of"),
            "source_observed_at": aging_report.get("source_observed_at"),
            "currency": aging_report.get("currency"),
            "totals": None,
            "priorities": [],
            "exceptions": exceptions,
            "reconciliation_status": aging_report.get(
                "reconciliation_status", "not_reconciled_to_payments_or_gl"
            ),
            "accounting_reconciled": False,
            "external_communication_authorized": False,
        }

    contributions = aging_report.get("contributions") or []
    grouped: dict[str, dict] = {}

    for entry in contributions:
        if not isinstance(entry, Mapping):
            raise ValueError("Aging contribution must be a mapping")
        cid = str(entry["customer_id"])
        inv_id = str(entry["source_invoice_id"])
        bucket = str(entry["bucket"])
        balance = _parse_non_negative_decimal(entry.get("balance"), "contribution.balance")
        acc = grouped.setdefault(
            cid,
            {
                "customer_id": cid,
                "customer_name": names.get(cid, cid),
                "total_open": Decimal("0"),
                "current_amount": Decimal("0"),
                "overdue_amount": Decimal("0"),
                "days_31_60_amount": Decimal("0"),
                "severe_overdue_amount": Decimal("0"),
                "unaged_amount": Decimal("0"),
                "disputed_amount": Decimal("0"),
                "collectible_overdue_amount": Decimal("0"),
                "open_invoice_count": 0,
                "disputed_invoice_ids": [],
            },
        )
        acc["total_open"] += balance
        acc["open_invoice_count"] += 1
        is_disputed = inv_id in disputed_set
        if is_disputed:
            acc["disputed_amount"] += balance
            acc["disputed_invoice_ids"].append(inv_id)
        if bucket == "current":
            acc["current_amount"] += balance
        elif bucket == "unaged":
            acc["unaged_amount"] += balance
        elif bucket in _OVERDUE_BUCKETS:
            acc["overdue_amount"] += balance
            if not is_disputed:
                acc["collectible_overdue_amount"] += balance
                if bucket == "days_31_60":
                    acc["days_31_60_amount"] += balance
                if bucket in _SEVERE_BUCKETS:
                    acc["severe_overdue_amount"] += balance

    priorities: list[dict] = []
    total_open = Decimal("0")
    total_overdue = Decimal("0")
    total_disputed = Decimal("0")
    total_collectible_overdue = Decimal("0")

    for cid, acc in grouped.items():
        acc["disputed_invoice_ids"].sort()
        credit_limit = parsed_limits.get(cid)
        credit_utilization_pct: Decimal | None = None
        over_credit_limit = False
        if credit_limit is not None:
            over_credit_limit = acc["total_open"] > credit_limit
            if credit_limit > 0:
                credit_utilization_pct = (
                    (acc["total_open"] / credit_limit) * Decimal("100")
                ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

        promise_status, promise_due = _resolve_promise_status(promises.get(cid), as_of)
        collectible_overdue = acc["collectible_overdue_amount"]

        reasons: list[str] = []
        if acc["disputed_amount"] > 0:
            reasons.append(
                f"disputed_holdback={acc['disputed_amount'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"
            )
        if acc["severe_overdue_amount"] > 0:
            reasons.append("severe_overdue_61_plus_days")
        elif acc["days_31_60_amount"] > 0:
            reasons.append("overdue_31_60_days")
        elif collectible_overdue > 0:
            reasons.append("overdue_1_30_days")
        if over_credit_limit:
            reasons.append("over_credit_limit")
        elif credit_utilization_pct is not None and credit_utilization_pct >= Decimal("85.0"):
            reasons.append("high_credit_utilization")
        if promise_status == "broken":
            reasons.append("broken_promise_to_pay")
        elif promise_status == "active":
            reasons.append("active_promise_to_pay")
        if acc["unaged_amount"] > 0:
            reasons.append("unaged_due_date_missing")

        if collectible_overdue == 0 and acc["disputed_amount"] > 0:
            priority = "disputed_hold"
            recommended_action = "resolve_billing_dispute_before_dunning"
        elif collectible_overdue > 0 and (
            acc["severe_overdue_amount"] > 0
            or over_credit_limit
            or promise_status == "broken"
        ):
            priority = "critical"
            recommended_action = "escalate_for_human_approved_collection_call"
        elif collectible_overdue > 0 and (
            acc["days_31_60_amount"] > 0
            or (credit_utilization_pct is not None and credit_utilization_pct >= Decimal("85.0"))
        ):
            priority = "high"
            recommended_action = "draft_payment_reminder_for_approval"
        elif collectible_overdue > 0 or acc["unaged_amount"] > 0:
            priority = "medium"
            recommended_action = "review_due_dates_and_draft_follow_up"
        else:
            priority = "monitor"
            recommended_action = "monitor_current_balance"

        total_open += acc["total_open"]
        total_overdue += acc["overdue_amount"]
        total_disputed += acc["disputed_amount"]
        total_collectible_overdue += collectible_overdue

        priorities.append({
            **acc,
            "credit_limit": credit_limit,
            "credit_utilization_pct": credit_utilization_pct,
            "over_credit_limit": over_credit_limit,
            "promise_status": promise_status,
            "promise_due_date": promise_due,
            "priority": priority,
            "recommended_action": recommended_action,
            "reasons": reasons,
            "responsible_employee": owners.get(cid),
        })

    priorities.sort(
        key=lambda row: (
            _PRIORITY_RANK[row["priority"]],
            -row["collectible_overdue_amount"],
            -row["total_open"],
            row["customer_id"],
        )
    )

    return {
        "status": "partial_collections_review" if exceptions else "available_unreconciled",
        "tenant_id": tenant_id,
        "as_of": as_of.isoformat(),
        "source_as_of": aging_report.get("source_as_of"),
        "source_observed_at": aging_report.get("source_observed_at"),
        "currency": aging_report.get("currency"),
        "totals": {
            "total_open": total_open,
            "total_overdue": total_overdue,
            "total_disputed": total_disputed,
            "total_collectible_overdue": total_collectible_overdue,
        },
        "priorities": priorities,
        "exceptions": exceptions,
        "reconciliation_status": aging_report.get(
            "reconciliation_status", "not_reconciled_to_payments_or_gl"
        ),
        "accounting_reconciled": False,
        "external_communication_authorized": False,
    }
