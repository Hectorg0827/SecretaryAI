"""
Admin API — audit log and system diagnostics for owners and managers.

Endpoints
---------
GET  /api/admin/audit-log       — Paginated, filterable audit event log
GET  /api/admin/system-health   — Aggregate status across all subsystems
GET  /api/admin/workflow-runs   — Recent workflow runs with step detail
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_db, get_current_user

log = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_ROLES = {"owner", "manager"}


def _require_admin(user: dict) -> dict:
    if user.get("role") not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ─── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    event_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    since: Optional[str] = None,          # ISO timestamp
    until: Optional[str] = None,          # ISO timestamp
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Return a paginated audit log for the company.

    Filters (all optional):
      event_type — exact match on event_type column
      actor_id   — exact match on actor_id column
      since      — only events created_at >= since (ISO 8601)
      until      — only events created_at <= until (ISO 8601)
    """
    _require_admin(user)
    company_id = user["company_id"]
    offset = (page - 1) * page_size

    try:
        q = (
            db.table("audit_events")
            .select("*", count="exact")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
        )
        if event_type:
            q = q.eq("event_type", event_type)
        if actor_id:
            q = q.eq("actor_id", actor_id)
        if since:
            q = q.gte("created_at", since)
        if until:
            q = q.lte("created_at", until)

        result = q.range(offset, offset + page_size - 1).execute()
    except Exception as exc:
        log.error("audit-log query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    total = result.count or 0
    events = result.data or []

    return {
        "events": events,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),  # ceiling division
    }


# ─── System health ────────────────────────────────────────────────────────────

@router.get("/system-health")
async def get_system_health(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Aggregate health status across all platform subsystems.

    Returns counts and recency for:
      - connectors        (connected / stale / error)
      - workflow_runs     (running / completed / failed in last 24h)
      - computer_use_jobs (pending / running / completed / failed in last 24h)
      - ingestion_queue   (pending / processing / done / error in last 24h)
      - audit_events      (count in last 24h)
    """
    _require_admin(user)
    company_id = user["company_id"]
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    health: dict = {
        "company_id": company_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "connectors": _connector_health(db, company_id),
        "workflow_runs": _workflow_health(db, company_id, since_24h),
        "computer_use": _cu_health(db, company_id, since_24h),
        "ingestion": _ingestion_health(db, company_id, since_24h),
        "audit_events_24h": _audit_count(db, company_id, since_24h),
    }
    return health


def _connector_health(db, company_id: str) -> dict:
    try:
        result = (
            db.table("connector_registrations")
            .select("status")
            .eq("company_id", company_id)
            .execute()
        )
        rows = result.data or []
        counts: dict[str, int] = {}
        for r in rows:
            s = r.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return {
            "connected": counts.get("connected", 0),
            "stale":     counts.get("stale",     0),
            "error":     counts.get("error",      0),
            "total":     len(rows),
        }
    except Exception as exc:
        log.warning("connector_health query failed: %s", exc)
        return {"error": str(exc)}


def _workflow_health(db, company_id: str, since: str) -> dict:
    try:
        result = (
            db.table("workflow_runs")
            .select("status")
            .eq("company_id", company_id)
            .gte("created_at", since)
            .execute()
        )
        rows = result.data or []
        counts: dict[str, int] = {}
        for r in rows:
            s = r.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return {
            "running":            counts.get("running",            0),
            "awaiting_approval":  counts.get("awaiting_approval",  0),
            "completed":          counts.get("completed",          0),
            "failed":             counts.get("failed",             0),
            "total_24h":          len(rows),
        }
    except Exception as exc:
        log.warning("workflow_health query failed: %s", exc)
        return {"error": str(exc)}


def _cu_health(db, company_id: str, since: str) -> dict:
    try:
        result = (
            db.table("computer_use_jobs")
            .select("status")
            .eq("company_id", company_id)
            .gte("created_at", since)
            .execute()
        )
        rows = result.data or []
        counts: dict[str, int] = {}
        for r in rows:
            s = r.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return {
            "pending":   counts.get("pending",   0),
            "running":   counts.get("running",   0),
            "completed": counts.get("completed", 0),
            "failed":    counts.get("failed",    0),
            "total_24h": len(rows),
        }
    except Exception as exc:
        log.warning("cu_health query failed: %s", exc)
        return {"error": str(exc)}


def _ingestion_health(db, company_id: str, since: str) -> dict:
    try:
        result = (
            db.table("ingestion_queue")
            .select("status")
            .eq("company_id", company_id)
            .gte("first_seen_at", since)
            .execute()
        )
        rows = result.data or []
        counts: dict[str, int] = {}
        for r in rows:
            s = r.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return {
            "pending":    counts.get("pending",    0),
            "processing": counts.get("processing", 0),
            "done":       counts.get("done",       0),
            "error":      counts.get("error",      0),
            "total_24h":  len(rows),
        }
    except Exception as exc:
        log.warning("ingestion_health query failed: %s", exc)
        return {"error": str(exc)}


def _audit_count(db, company_id: str, since: str) -> int:
    try:
        result = (
            db.table("audit_events")
            .select("id", count="exact")
            .eq("company_id", company_id)
            .gte("created_at", since)
            .execute()
        )
        return result.count or 0
    except Exception as exc:
        log.warning("audit_count query failed: %s", exc)
        return 0


# ─── Workflow runs ────────────────────────────────────────────────────────────

@router.get("/workflow-runs")
async def get_workflow_runs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status: Optional[str] = None,
    workflow_name: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Return recent workflow runs for the company, newest first.
    Optionally filter by status or workflow_name.
    """
    _require_admin(user)
    company_id = user["company_id"]
    offset = (page - 1) * page_size

    try:
        q = (
            db.table("workflow_runs")
            .select("*", count="exact")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
        )
        if status:
            q = q.eq("status", status)
        if workflow_name:
            q = q.eq("workflow_name", workflow_name)

        result = q.range(offset, offset + page_size - 1).execute()
    except Exception as exc:
        log.error("workflow-runs query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    total = result.count or 0
    return {
        "runs": result.data or [],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }
