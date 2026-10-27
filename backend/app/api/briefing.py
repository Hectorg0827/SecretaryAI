"""Read-only owner/manager briefing built from the tenant's cached dashboard.

This is the first *partial* v1 briefing endpoint. It does not claim that
opportunities, actions taken, or real-time provider state were inspected.
No live connector calls, outbound messages, or action execution occur.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_db
from app.auth.rbac import require_permission
from app.domain.briefing_contract import (
    BriefingSection, DigestEntry, EvidenceRef, RoleBriefing,
)
from app.domain.briefing_wire import BriefingTodayResponseV1
from app.scheduler.dashboard_snapshot import read_snapshot, snapshot_freshness, SnapshotReadUnavailable

router = APIRouter()


class BriefingSourceUnavailable(ValueError):
    """No usable, recent, source-qualified company snapshot is available."""


def _count(obj: dict, key: str) -> int:
    value = obj.get(key)
    if type(value) is not int or value < 0:
        raise BriefingSourceUnavailable(f"Invalid summary count: {key}")
    return value


def _snapshot_time(raw) -> datetime:
    if not isinstance(raw, (str, datetime)):
        raise BriefingSourceUnavailable("Missing snapshot timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else raw
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timezone required")
    except (TypeError, ValueError, OverflowError) as exc:
        raise BriefingSourceUnavailable("Invalid snapshot timestamp") from exc
    return parsed


def build_owner_briefing(
    *, company_id: str, role: str, snapshot: dict, now: datetime | None = None,
) -> dict:
    """Strict read-only aggregate projection; never includes rep/customer records."""
    if role not in ("owner", "manager"):
        raise PermissionError("Detailed company briefing requires executive role")
    if not company_id or not isinstance(snapshot, dict):
        raise BriefingSourceUnavailable("Company snapshot unavailable")
    payload = snapshot.get("payload")
    if not isinstance(payload, dict) or payload.get("source_read_status") != "completed":
        raise BriefingSourceUnavailable("Incomplete source snapshot")
    # V1 has no independently verified provider-lineage certificate. A
    # cached row cannot self-promote to "verified" merely by changing a flag.
    if payload.get("source_quality") != "partial":
        raise BriefingSourceUnavailable("Briefing snapshot provenance unverified")
    current = now or datetime.now(timezone.utc)
    observed = _snapshot_time(snapshot.get("generated_at"))
    freshness = snapshot_freshness(observed, now=current)
    if freshness["status"] != "fresh":
        raise BriefingSourceUnavailable("Snapshot is stale or invalid")

    accounts = payload.get("accounts")
    inventory = payload.get("inventory_alerts")
    sales = payload.get("sales_30d")
    if not isinstance(accounts, dict) or not isinstance(inventory, list):
        raise BriefingSourceUnavailable("Snapshot fields are incomplete")
    counts = {key: _count(accounts, key) for key in
              ("healthy", "slowing", "at_risk", "dormant", "total")}
    if sum(counts[x] for x in ("healthy", "slowing", "at_risk", "dormant")) != counts["total"]:
        raise BriefingSourceUnavailable("Account aggregate mismatch")
    if type(sales) not in (int, float):
        raise BriefingSourceUnavailable("Invalid sales figure")
    try:
        finite_sales = isfinite(float(sales))
    except (ValueError, OverflowError, TypeError):
        finite_sales = False
    if not finite_sales:
        raise BriefingSourceUnavailable("Invalid sales figure")
    for item in inventory:
        if not isinstance(item, dict) or item.get("stock_status") not in (
            "critical", "out_of_stock", "low"
        ):
            raise BriefingSourceUnavailable("Invalid inventory alert")
    critical = sum(i["stock_status"] in ("critical", "out_of_stock") for i in inventory)
    low = sum(i["stock_status"] == "low" for i in inventory)

    evidence = (EvidenceRef(
        source_system="dashboard_snapshot",
        source_record_id=f"{company_id}:dashboard_summary:{observed.isoformat()}",
        fetched_at=observed, quality="partial", confidence=40,
    ),)
    scope = frozenset({"owner", "manager"})
    entries: list[DigestEntry] = []

    def add(key: str, section: BriefingSection, title: str) -> None:
        entries.append(DigestEntry(
            company_id=company_id, entry_id=key, section=section,
            title=title, role_scope=scope, evidence=evidence,
            detected_at=observed, status="needs_review",
        ))

    if counts["at_risk"] or counts["dormant"] or critical:
        add("aggregate-risk", BriefingSection.RED_ALERTS,
            f"{counts['at_risk']} at-risk accounts, {counts['dormant']} dormant accounts, "
            f"{critical} critical inventory alerts (snapshot only)")
    if counts["slowing"] or low:
        add("aggregate-watch", BriefingSection.YELLOW_ALERTS,
            f"{counts['slowing']} slowing accounts and {low} low-stock alerts (snapshot only)")
    add("aggregate-scoreboard", BriefingSection.SCOREBOARD,
        f"Last 30-day sales in snapshot: {float(sales):.2f} "
        f"across {counts['total']} classified accounts (source not independently verified)")

    briefing = RoleBriefing.project(
        company_id=company_id, audience_role=role, created_at=current,
        entries=entries,
    )
    response = briefing.to_payload()
    response.update({
        "source_coverage": "dashboard_snapshot_only",
        "complete_digest": False,
        "source_quality": "partial",
        "snapshot_generated_at": observed.isoformat(),
        "snapshot_age_seconds": freshness["age_seconds"],
    })
    return response


@router.get("/today", response_model=BriefingTodayResponseV1)
async def get_today_briefing(
    user: dict = Depends(require_permission("view_financials")),
    db=Depends(get_db),
):
    """Returns partial executive briefing; no provider access or side effects."""
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Executive briefing not authorized")
    company_id = user.get("company_id", "")
    if not company_id:
        raise HTTPException(status_code=403, detail="Invalid company session")
    try:
        snapshot = read_snapshot(db, company_id, "dashboard_summary")
    except SnapshotReadUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Company briefing cache unavailable"
        ) from exc
    try:
        return build_owner_briefing(
            company_id=company_id, role=user["role"], snapshot=snapshot,
        )
    except BriefingSourceUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Company briefing source unavailable or stale",
        ) from exc
