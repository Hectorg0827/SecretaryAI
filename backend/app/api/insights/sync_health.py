"""Sync health: do the tenant's connected systems agree?

Read-only view over the ``sync_checks`` rows written by the scheduled
reconciliation (``app/scheduler/sync_check.py``). Nothing here re-runs a
check, contacts a provider or changes data.

The page must never imply "all good" without evidence, so every check is
classified explicitly:

- ``never_run``        no check has been recorded for this company yet
- ``in_sync``          two systems were compared and agreed
- ``not_compared``     only one system could be read, so nothing was compared
- ``mismatches_found`` the systems disagree
- ``check_failed``     the check could not read its sources
- ``unknown``          a recorded status this endpoint does not recognise
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.insights._common import advisory_response, read_source
from app.auth.rbac import require_permission

PREFIX = "/sync-health"
TAG = "insights-sync-health"

router = APIRouter()

# The scheduler runs every 4 hours; three missed runs means it is not running.
STALE_AFTER_HOURS = 12
# Enough rows to find the latest result of each check type plus a short history.
_FETCH_LIMIT = 50
_HISTORY_LIMIT = 10

_CHECK_LABELS = {
    "inventory_reconciliation": "Stock counts: QuickBooks vs warehouse system",
    "revenue_reconciliation": "Sales totals: QuickBooks vs ordering system",
    "qb_inventory": "Stock counts in QuickBooks",
    "qb_accounts": "Customer accounts in QuickBooks",
}
_SYSTEMS_COMPARED = {
    "inventory_reconciliation": ["QuickBooks", "Warehouse system"],
    "revenue_reconciliation": ["QuickBooks", "Ordering system"],
}
# Higher wins when summarising several check types into one headline.
_STATE_PRIORITY = {
    "in_sync": 1,
    "not_compared": 2,
    "unknown": 3,
    "check_failed": 4,
    "mismatches_found": 5,
}


class _SyncChecksUnavailable(Exception):
    """The sync_checks read returned something other than a list of rows."""


def _label(check_type: str) -> str:
    if check_type in _CHECK_LABELS:
        return _CHECK_LABELS[check_type]
    return check_type.replace("_", " ").strip().capitalize() or "Data check"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mismatches(details: dict) -> list[dict]:
    raw = details.get("discrepancies")
    if not isinstance(raw, list):
        return []
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        severity = entry.get("severity")
        result.append({
            "item": str(entry.get("field") or ""),
            "severity": severity if severity in ("info", "warning", "critical") else "warning",
            "message": str(entry.get("message") or ""),
            "difference_pct": _as_float(entry.get("variance_pct")),
        })
    return result


def _classify(row: dict) -> dict:
    """Turn one stored sync_checks row into a plain, explicit result."""
    check_type = str(row.get("check_type") or "unknown")
    status = str(row.get("status") or "unknown").lower()
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    mismatches = _mismatches(details)
    comparisons_run = _as_int(details.get("checks_run"))
    # Legacy rows carry the compared values in columns instead of details.
    compared_in_columns = (
        _as_float(row.get("source_1_value")) is not None
        and _as_float(row.get("source_2_value")) is not None
    )

    if status == "error":
        state = "check_failed"
    elif status in ("warning", "critical"):
        state = "mismatches_found"
    elif status == "broken":
        state = "mismatches_found" if mismatches else "check_failed"
    elif status == "healthy":
        if mismatches:
            state = "mismatches_found"
        elif comparisons_run is not None:
            # One read is QuickBooks alone; agreement needs a second system.
            state = "in_sync" if comparisons_run >= 2 else "not_compared"
        else:
            state = "in_sync" if compared_in_columns else "not_compared"
    else:
        state = "unknown"

    return {
        "check_type": check_type,
        "label": _label(check_type),
        "systems_compared": _SYSTEMS_COMPARED.get(check_type, []),
        "recorded_status": status,
        "state": state,
        "checked_at": row.get("checked_at"),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


@router.get("")
async def get_sync_health(
    user: dict = Depends(require_permission("view_all")),
    db=Depends(get_db),
):
    """Latest sync check results for the signed-in user's company."""
    company_id = user["company_id"]

    def _read() -> list[dict]:
        result = (
            db.table("sync_checks")
            .select("id,company_id,check_type,status,details,checked_at,"
                    "source_1_value,source_2_value,variance_pct")
            .eq("company_id", company_id)
            .order("checked_at", desc=True)
            .limit(_FETCH_LIMIT)
            .execute()
        )
        rows = getattr(result, "data", None)
        if not isinstance(rows, list):
            raise _SyncChecksUnavailable()
        return rows

    rows = await read_source(asyncio.to_thread(_read), source_name="Sync check history")
    # Defence in depth: never surface a row the query should not have matched.
    rows = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("company_id")) == str(company_id)
    ]

    if not rows:
        return advisory_response({
            "state": "never_run",
            "has_run": False,
            "last_checked_at": None,
            "stale": False,
            "stale_after_hours": STALE_AFTER_HOURS,
            "checks": [],
            "history": [],
        }, source="connected_data")

    classified = [_classify(row) for row in rows]
    latest_by_type: dict[str, dict] = {}
    for check in classified:  # rows arrive newest first
        latest_by_type.setdefault(check["check_type"], check)
    checks = list(latest_by_type.values())

    last_checked_at = classified[0]["checked_at"]
    last_time = _parse_time(last_checked_at)
    stale = (
        last_time is None
        or datetime.now(timezone.utc) - last_time > timedelta(hours=STALE_AFTER_HOURS)
    )
    state = max((c["state"] for c in checks), key=lambda s: _STATE_PRIORITY.get(s, 3))

    return advisory_response({
        "state": state,
        "has_run": True,
        "last_checked_at": last_checked_at,
        "stale": stale,
        "stale_after_hours": STALE_AFTER_HOURS,
        "checks": checks,
        "history": [
            {
                "check_type": c["check_type"],
                "label": c["label"],
                "state": c["state"],
                "checked_at": c["checked_at"],
                "mismatch_count": c["mismatch_count"],
            }
            for c in classified[:_HISTORY_LIMIT]
        ],
    }, source="connected_data")
