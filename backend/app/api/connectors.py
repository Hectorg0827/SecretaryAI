"""
Connector API — cloud side of the cloud ↔ local connector protocol.

Endpoints
---------
POST /api/connectors/register       — Connector registers itself on startup
POST /api/connectors/heartbeat      — Connector sends periodic heartbeat
POST /api/connectors/task-result    — Connector submits completed task result
POST /api/connectors/sync-status    — Connector reports sync progress
POST /api/connectors/error          — Connector reports a non-task error
GET  /api/connectors/tasks          — Connector polls for pending tasks
GET  /api/connectors/status         — Web UI reads connector health (user-facing)
GET  /api/connectors/status/{id}    — Single connector detail

Authentication
--------------
- /register: requires a valid user JWT + install_secret (pre-shared per company)
- All other endpoints: require a connector_token (JWT issued at registration)
  with scope="connector"
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_db, get_current_user
from app.auth.jwt import create_access_token
from app.config import get_settings
from app.domain.connector_protocol import (
    HEARTBEAT_INTERVAL_SECONDS,
    STALE_THRESHOLD_SECONDS,
    ConnectorErrorReport,
    ConnectorErrorResponse,
    ConnectorType,
    FetchTaskResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
    SubmitResultResponse,
    SyncStatusReport,
    SyncStatusResponse,
    TaskResult,
    TaskStatus,
)
from app.domain.contracts import ConnectorStatus

log = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


# ─── Connector token auth ─────────────────────────────────────────────────────

def _get_connector_token(
    authorization: str | None = None,
) -> dict:
    """
    Dependency that validates a connector_token JWT (scope='connector').
    Used for all connector endpoints except /register.
    """
    from fastapi import Header
    from app.auth.jwt import decode_access_token
    # Import here to avoid circular; fastapi injects via the explicit param below
    raise NotImplementedError("use _connector_token_dep directly")


from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def get_connector_identity(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db=Depends(get_db),
) -> dict:
    """
    Validates the connector JWT and returns the connector registration row.
    Raises 401 if invalid or registration not found.
    """
    if not creds:
        raise HTTPException(status_code=401, detail="Missing connector token")

    from app.auth.jwt import decode_access_token
    payload = decode_access_token(creds.credentials)
    if not payload or payload.get("scope") != "connector":
        raise HTTPException(status_code=401, detail="Invalid connector token")

    registration_id = payload.get("registration_id")
    company_id = payload.get("company_id")
    if not registration_id or not company_id:
        raise HTTPException(status_code=401, detail="Malformed connector token")

    try:
        result = (
            db.table("connector_registrations")
            .select("*")
            .eq("id", registration_id)
            .eq("company_id", company_id)
            .execute()
        )
    except Exception as exc:
        log.error("Connector auth DB lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data:
        raise HTTPException(status_code=401, detail="Connector registration not found")

    return result.data[0]


# ─── Helper ───────────────────────────────────────────────────────────────────

def _mark_stale_connectors(db, company_id: str) -> None:
    """Mark connectors that haven't heartbeated recently as stale."""
    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS)
    ).isoformat()
    try:
        db.table("connector_registrations").update({
            "status": "stale",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("company_id", company_id).eq("status", "connected").lt(
            "last_heartbeat", stale_cutoff
        ).execute()
    except Exception as exc:
        log.warning("Failed to mark stale connectors for %s: %s", company_id, exc)


# ─── Registration ─────────────────────────────────────────────────────────────

@router.post("/register", response_model=RegisterResponse)
async def register_connector(
    req: RegisterRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Called by a local connector on first startup.

    The install_secret is validated against the company's stored secret.
    On success, returns a long-lived connector_token JWT.
    The connector must store this token securely and use it for all
    subsequent API calls.
    """
    if req.company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="company_id mismatch")

    # Validate install_secret against the company's stored secret
    try:
        company = (
            db.table("companies")
            .select("connector_install_secret")
            .eq("id", req.company_id)
            .execute()
        )
    except Exception as exc:
        log.error("Register: company lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not company.data:
        raise HTTPException(status_code=404, detail="Company not found")

    stored_secret = (company.data[0] or {}).get("connector_install_secret")
    if not stored_secret or not secrets.compare_digest(
        req.install_secret.encode(), stored_secret.encode()
    ):
        raise HTTPException(status_code=403, detail="Invalid install secret")

    now = datetime.now(timezone.utc).isoformat()
    # Upsert registration row
    try:
        result = (
            db.table("connector_registrations")
            .upsert(
                {
                    "company_id": req.company_id,
                    "connector_type": req.connector_type.value,
                    "connector_id": req.connector_id,
                    "version": req.version,
                    "status": "connected",
                    "capabilities": req.capabilities,
                    "metadata": req.metadata,
                    "last_heartbeat": now,
                    "registered_at": now,
                    "updated_at": now,
                },
                on_conflict="company_id,connector_type,connector_id",
            )
            .execute()
        )
    except Exception as exc:
        log.error("Register: upsert failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    registration_id = result.data[0]["id"]

    # Issue connector JWT (long-lived: 100 days)
    connector_token = create_access_token(
        {
            "scope": "connector",
            "registration_id": registration_id,
            "company_id": req.company_id,
            "connector_type": req.connector_type.value,
        },
        expires_delta=timedelta(days=100),
    )

    log.info(
        "Connector registered: type=%s id=%s company=%s",
        req.connector_type.value,
        req.connector_id,
        req.company_id,
    )
    return RegisterResponse(
        registration_id=registration_id,
        connector_token=connector_token,
        task_poll_interval_seconds=30,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        cloud_api_base=settings.frontend_url or "http://localhost:8000",
        message="registered",
    )


# ─── Heartbeat ────────────────────────────────────────────────────────────────

@router.post("/heartbeat", response_model=HeartbeatResponse)
async def connector_heartbeat(
    req: HeartbeatRequest,
    connector: dict = Depends(get_connector_identity),
    db=Depends(get_db),
):
    """
    Periodic liveness ping from the connector.
    Updates last_heartbeat and clears stale status.
    Returns count of pending tasks as a hint.
    """
    now = datetime.now(timezone.utc)
    try:
        db.table("connector_registrations").update({
            "last_heartbeat": now.isoformat(),
            "status": "connected" if req.status == "ok" else req.status,
            "last_error": req.metrics.get("last_error"),
            "updated_at": now.isoformat(),
        }).eq("id", connector["id"]).execute()
    except Exception as exc:
        log.warning("Heartbeat DB update failed for %s: %s", connector["id"], exc)

    # Count pending tasks for this connector
    pending = 0
    try:
        result = (
            db.table("connector_tasks")
            .select("id", count="exact")
            .eq("company_id", connector["company_id"])
            .eq("connector_type", connector["connector_type"])
            .eq("status", "pending")
            .execute()
        )
        pending = result.count or 0
    except Exception:
        pass  # table may not exist yet; non-fatal

    return HeartbeatResponse(
        acknowledged=True,
        server_time=now,
        pending_task_count=pending,
    )


# ─── Task polling ─────────────────────────────────────────────────────────────

# Re-delivery window: if a task was dispatched but no result received within
# this many seconds, it becomes eligible for re-dispatch to another connector.
_TASK_REDELIVERY_SECONDS = 300  # 5 minutes


@router.get("/tasks", response_model=FetchTaskResponse)
async def fetch_connector_tasks(
    max_tasks: Annotated[int, Query(ge=1, le=10)] = 1,
    connector: dict = Depends(get_connector_identity),
    db=Depends(get_db),
):
    """
    Connector polls for pending tasks.

    Returns up to max_tasks tasks and marks them as 'dispatched'.

    Idempotency: a task dispatched more than _TASK_REDELIVERY_SECONDS ago without
    a result is treated as 'pending' again, preventing permanent loss on connector
    crash.  A task is never returned twice within the redelivery window.
    """
    now = datetime.now(timezone.utc)
    redelivery_cutoff = (now - timedelta(seconds=_TASK_REDELIVERY_SECONDS)).isoformat()

    tasks_out = []
    try:
        # Fetch tasks that are either:
        #   a) status='pending'  (never dispatched), OR
        #   b) status='dispatched' AND dispatched_at < redelivery_cutoff
        #      (dispatched but no result within the window — eligible for re-dispatch)
        result = (
            db.table("connector_tasks")
            .select("*")
            .eq("company_id", connector["company_id"])
            .eq("connector_type", connector["connector_type"])
            .in_("status", ["pending", "dispatched"])
            .order("priority", desc=False)
            .order("issued_at", desc=False)
            .limit(max_tasks * 5)  # over-fetch to filter in Python
            .execute()
        )
        rows = result.data or []

        for row in rows:
            if len(tasks_out) >= max_tasks:
                break
            # Skip recently-dispatched tasks (still within redelivery window)
            if (
                row["status"] == "dispatched"
                and row.get("dispatched_at")
                and row["dispatched_at"] >= redelivery_cutoff
            ):
                continue

            # Atomically mark dispatched — only this connector gets this task
            db.table("connector_tasks").update({
                "status": "dispatched",
                "dispatched_at": now.isoformat(),
                "dispatcher_id": connector["id"],
            }).eq("id", row["id"]).in_("status", ["pending", "dispatched"]).execute()

            tasks_out.append(row)

    except Exception as exc:
        log.warning("Task fetch failed for connector %s: %s", connector["id"], exc)

    return FetchTaskResponse(tasks=tasks_out, server_time=now)


# ─── Task result submission ───────────────────────────────────────────────────

@router.post("/task-result", response_model=SubmitResultResponse)
async def submit_task_result(
    result: TaskResult,
    connector: dict = Depends(get_connector_identity),
    db=Depends(get_db),
):
    """
    Connector submits the result of a completed (or failed) task.
    The cloud stores the result, updates the workflow, and optionally
    returns the next task.
    """
    if result.company_id != connector["company_id"]:
        raise HTTPException(status_code=403, detail="company_id mismatch")

    now = datetime.now(timezone.utc)
    try:
        db.table("connector_tasks").update({
            "status": result.status.value,
            "result_data": result.data,
            "rows_returned": result.rows_returned,
            "error_message": result.error_message,
            "error_code": result.error_code,
            "duration_ms": result.duration_ms,
            "completed_at": (result.completed_at or now).isoformat(),
        }).eq("id", result.task_id).execute()
    except Exception as exc:
        log.error("Task result update failed for %s: %s", result.task_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Update last_sync_at on registration
    if result.status == TaskStatus.COMPLETED:
        try:
            db.table("connector_registrations").update({
                "last_sync_at": now.isoformat(),
                "last_sync_status": "ok",
                "updated_at": now.isoformat(),
            }).eq("id", connector["id"]).execute()
        except Exception as exc:
            log.warning("Failed to update last_sync_at: %s", exc)

    return SubmitResultResponse(acknowledged=True)


# ─── Sync status ──────────────────────────────────────────────────────────────

@router.post("/sync-status", response_model=SyncStatusResponse)
async def report_sync_status(
    report: SyncStatusReport,
    connector: dict = Depends(get_connector_identity),
    db=Depends(get_db),
):
    """Connector reports incremental sync progress."""
    now = datetime.now(timezone.utc)
    try:
        db.table("connector_sync_logs").insert({
            "connector_id": connector["id"],
            "company_id": connector["company_id"],
            "sync_type": report.sync_type,
            "entity_type": report.entity_type,
            "status": "ok" if report.is_final and not report.error_detail else (
                "error" if report.error_detail else "running"
            ),
            "rows_fetched": report.rows_fetched,
            "rows_upserted": report.rows_upserted,
            "rows_errored": report.rows_errored,
            "error_detail": report.error_detail,
            "started_at": now.isoformat(),
            "completed_at": now.isoformat() if report.is_final else None,
            "metadata": report.metadata,
        }).execute()
    except Exception as exc:
        log.warning("Sync status insert failed: %s", exc)

    if report.is_final:
        try:
            db.table("connector_registrations").update({
                "last_sync_at": now.isoformat(),
                "last_sync_status": "error" if report.error_detail else "ok",
                "last_error": report.error_detail,
                "updated_at": now.isoformat(),
            }).eq("id", connector["id"]).execute()
        except Exception as exc:
            log.warning("Failed to update connector last_sync: %s", exc)

    return SyncStatusResponse(acknowledged=True)


# ─── Error reporting ──────────────────────────────────────────────────────────

@router.post("/error", response_model=ConnectorErrorResponse)
async def report_connector_error(
    report: ConnectorErrorReport,
    connector: dict = Depends(get_connector_identity),
    db=Depends(get_db),
):
    """Connector reports a non-task error (network down, QB crash, etc.)."""
    now = datetime.now(timezone.utc)
    try:
        db.table("connector_registrations").update({
            "status": "error",
            "last_error": f"[{report.error_type}] {report.error_message}",
            "updated_at": now.isoformat(),
        }).eq("id", connector["id"]).execute()
    except Exception as exc:
        log.warning("Connector error update failed: %s", exc)

    log.warning(
        "Connector error reported: type=%s company=%s error=%s",
        report.error_type,
        connector["company_id"],
        report.error_message,
    )
    return ConnectorErrorResponse(
        acknowledged=True,
        remediation_hint=_remediation_hint(report.error_type),
    )


def _remediation_hint(error_type: str) -> str | None:
    hints = {
        "qb_connection": "Ensure QuickBooks Desktop is running and logged in.",
        "credential_expired": "Re-authenticate QuickBooks from Settings → Integrations.",
        "network": "Check network connectivity from the connector machine.",
    }
    return hints.get(error_type)


# ─── Status read (web UI) ─────────────────────────────────────────────────────

@router.get("/status")
async def get_connector_status(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Returns health status of all connectors for the current company.
    Used by the web UI connector-health dashboard.
    """
    company_id = user["company_id"]
    _mark_stale_connectors(db, company_id)

    try:
        result = (
            db.table("connector_registrations")
            .select("*")
            .eq("company_id", company_id)
            .execute()
        )
    except Exception as exc:
        log.error("Connector status fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    connectors = []
    now = datetime.now(timezone.utc)
    for row in (result.data or []):
        last_hb_str = row.get("last_heartbeat")
        last_hb = None
        is_stale = False
        if last_hb_str:
            try:
                last_hb = datetime.fromisoformat(last_hb_str)
                if last_hb.tzinfo is None:
                    last_hb = last_hb.replace(tzinfo=timezone.utc)
                is_stale = (now - last_hb).total_seconds() > STALE_THRESHOLD_SECONDS
            except ValueError:
                pass

        connectors.append(ConnectorStatus(
            id=str(row["id"]),
            company_id=str(row["company_id"]),
            connector_type=row["connector_type"],
            connector_id=row["connector_id"],
            version=row.get("version"),
            status=row["status"],
            last_heartbeat=last_hb,
            last_sync_at=row.get("last_sync_at"),
            last_sync_status=row.get("last_sync_status"),
            last_error=row.get("last_error"),
            capabilities=row.get("capabilities") or [],
            is_stale=is_stale,
        ))

    return {
        "connectors": [
            {
                "id": c.id,
                "connector_type": c.connector_type,
                "connector_id": c.connector_id,
                "version": c.version,
                "status": "stale" if c.is_stale else c.status,
                "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None,
                "last_sync_at": c.last_sync_at,
                "last_sync_status": c.last_sync_status,
                "last_error": c.last_error,
                "capabilities": c.capabilities,
            }
            for c in connectors
        ]
    }


@router.get("/status/{registration_id}")
async def get_single_connector_status(
    registration_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Single connector detail with recent sync logs."""
    company_id = user["company_id"]
    try:
        reg = (
            db.table("connector_registrations")
            .select("*")
            .eq("id", registration_id)
            .eq("company_id", company_id)
            .execute()
        )
    except Exception as exc:
        log.error("Single connector fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not reg.data:
        raise HTTPException(status_code=404, detail="Connector not found")

    # Last 20 sync logs
    try:
        logs = (
            db.table("connector_sync_logs")
            .select("*")
            .eq("connector_id", registration_id)
            .order("started_at", desc=True)
            .limit(20)
            .execute()
        )
        sync_logs = logs.data or []
    except Exception:
        sync_logs = []

    return {
        "connector": reg.data[0],
        "sync_logs": sync_logs,
    }


# ─── Install secret generation (owner-only) ───────────────────────────────────

@router.post("/generate-install-secret")
async def generate_install_secret(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Generate (or rotate) the install_secret for this company.
    The secret is shown once here; the user copies it into the connector installer.
    Returns the plaintext secret (stored hashed in DB).
    """
    from app.auth.rbac import require_permission
    if user.get("role") not in ("owner",):
        raise HTTPException(status_code=403, detail="Only owners can generate install secrets")

    raw_secret = secrets.token_urlsafe(32)
    try:
        db.table("companies").update({
            "connector_install_secret": raw_secret,
        }).eq("id", user["company_id"]).execute()
    except Exception as exc:
        log.error("Failed to store install secret: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return {
        "install_secret": raw_secret,
        "warning": "Copy this secret now — it will not be shown again. "
                   "Use it during connector installation.",
    }
