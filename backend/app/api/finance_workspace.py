"""Lola's strictly scoped, cached finance workspace (read-only staging gate).

No connector calls, individual customers/vendors, financial totals, or action
execution. A finance employee cannot ask this endpoint for another department.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_db
from app.auth.rbac import require_permission
from app.config import get_settings
from app.domain.finance_access import finance_scopes_for_role
from app.scheduler.dashboard_snapshot import (
    SnapshotReadUnavailable, read_snapshot, snapshot_freshness,
)
from app.scheduler.finance_comparison import FinanceReportInvalid, validate_finance_comparison
from app.scheduler.finance_document_coverage import (
    FinanceDocumentCoverageInvalid, validate_finance_document_coverage,
)

router = APIRouter()
Scope = Literal["ar", "ap", "ledger", "pricing"]
SourceStatus = Literal["observed", "unavailable", "not_assessed"]


class DepartmentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SourceStatus
    headers_examined: int | None = None
    open_count: int | None = None
    overdue_count: int | None = None
    due_date_missing_count: int | None = None
    variance_detected: bool | None = None
    note: str


class FinanceWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    authorized_scopes: list[Scope]
    snapshot_generated_at: str
    source_quality: Literal["partial"]
    currency_verified: Literal[False]
    ledger_reconciled: Literal[False]
    sections: dict[Scope, DepartmentSummary]


def _current_role(db, user: dict) -> str:
    """A signed JWT alone cannot preserve a revoked or changed finance role."""
    company = user.get("company_id")
    identity = user.get("sub")
    role = user.get("role")
    if not company or not identity or not finance_scopes_for_role(role):
        raise HTTPException(status_code=403, detail="Finance workspace not authorized")
    try:
        rows = (
            db.table("users").select("id,company_id,role,is_active")
            .eq("id", identity).eq("company_id", company)
            .limit(2).execute().data
        )
    except Exception:
        raise HTTPException(status_code=503, detail="Finance authorization unavailable") from None
    if (len(rows or []) != 1 or rows[0].get("company_id") != company
            or rows[0].get("id") != identity or rows[0].get("role") != role
            or rows[0].get("is_active") is not True):
        raise HTTPException(status_code=403, detail="Finance workspace not authorized")
    return role


def build_finance_workspace(*, role: str, snapshot: dict, now: datetime | None = None) -> dict:
    """Only role-approved aggregate sections are ever assembled or returned."""
    scopes = finance_scopes_for_role(role)
    if not scopes:
        raise PermissionError("Role has no finance scope")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("payload"), dict):
        raise ValueError("No finance snapshot")
    payload = snapshot["payload"]
    if payload.get("source_read_status") != "completed" or payload.get("source_quality") != "partial":
        raise ValueError("Finance snapshot source unqualified")
    observed_raw = snapshot.get("generated_at")
    try:
        observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Timestamp lacks timezone")
    except (TypeError, AttributeError, ValueError, OverflowError) as exc:
        raise ValueError("Finance snapshot timestamp invalid") from exc
    if snapshot_freshness(observed, now=now or datetime.now(timezone.utc))["status"] != "fresh":
        raise ValueError("Finance snapshot stale")

    sections: dict[str, dict] = {}
    if "ar" in scopes or "ap" in scopes:
        raw = payload.get("finance_document_coverage")
        if raw is not None:
            try:
                raw = validate_finance_document_coverage(raw)
            except FinanceDocumentCoverageInvalid as exc:
                raise ValueError("Invalid finance document evidence") from exc
        for kind in ("ar", "ap"):
            if kind not in scopes:
                continue
            source = raw[kind] if raw is not None else None
            if source is not None and source["status"] == "observed":
                sections[kind] = {
                    "status": "observed", "headers_examined": source["headers_examined"],
                    "open_count": source["open_count"], "overdue_count": source["overdue_count"],
                    "due_date_missing_count": source["due_date_missing_count"],
                    "note": "QuickBooks sandbox document counts only; balances not reconciled",
                }
            else:
                sections[kind] = {
                    "status": "unavailable" if source is not None else "not_assessed",
                    "note": "Sandbox document source has not produced trustworthy counts",
                }

    if "ledger" in scopes:
        report = payload.get("finance_comparison")
        if report is not None:
            try:
                report = validate_finance_comparison(report)
            except FinanceReportInvalid as exc:
                raise ValueError("Invalid ledger evidence") from exc
        observed_report = report is not None and report["status"] != "unavailable"
        sections["ledger"] = {
            "status": "observed" if observed_report else (
                "unavailable" if report is not None else "not_assessed"),
            "variance_detected": (report["status"] == "variance_detected") if observed_report else None,
            "note": "Ledger comparison is not a certified P&L or cash-flow statement",
        }
    if "pricing" in scopes:
        sections["pricing"] = {
            "status": "not_assessed",
            "note": "Supplier billbacks, cost approvals and price changes are not verified",
        }

    return FinanceWorkspaceResponse(
        role=role, authorized_scopes=sorted(scopes),
        snapshot_generated_at=observed.isoformat(), source_quality="partial",
        currency_verified=False, ledger_reconciled=False, sections=sections,
    ).model_dump()


@router.get("/workspace", response_model=FinanceWorkspaceResponse)
async def get_finance_workspace(
    response: Response,
    user: dict = Depends(require_permission("view_finance_workspace")),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    if get_settings().secretaryai_deployment_env.strip().lower() != "staging":
        raise HTTPException(status_code=404, detail="Not found")
    role = _current_role(db, user)
    try:
        snapshot = read_snapshot(db, user["company_id"], "dashboard_summary")
    except SnapshotReadUnavailable as exc:
        raise HTTPException(status_code=503, detail="Finance snapshot unavailable") from exc
    try:
        return build_finance_workspace(role=role, snapshot=snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Finance snapshot unavailable or stale") from exc
