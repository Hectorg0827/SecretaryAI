"""
Computer Use API — manages screen-reading sessions.

POST /api/computer-use/start          — start a new CU session (returns session_id)
GET  /api/computer-use/sessions/{id}  — poll status + pending approvals
POST /api/computer-use/approve        — approve a pending action
POST /api/computer-use/reject         — reject a pending action
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user, require_permission
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

# ── In-memory session registry ─────────────────────────────────────────────────
# Keyed by session_id. Cleaned up when the frontend confirms completion.

_sessions: dict[str, "_SessionState"] = {}


class _SessionState:
    def __init__(self, session_id: str, app_name: str, task: str, company_id: str):
        self.session_id = session_id
        self.app_name = app_name
        self.task = task
        self.company_id = company_id
        self.status: str = "running"      # running | waiting_approval | done | error
        self.steps_completed: int = 0
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.started_at = datetime.now(timezone.utc).isoformat()

        # Pending approval request (set by engine task when approval needed)
        self.pending_approval: Optional[dict] = None
        # Engine signals this when it needs a decision; router sets it after approve/reject
        self._approval_event: asyncio.Event = asyncio.Event()
        self._approval_decision: Optional[str] = None  # "approve" | "reject"

    # Called by the engine when it needs human approval before an action
    async def request_approval(self, action_type: str, description: str) -> str:
        self.pending_approval = {
            "id": str(uuid.uuid4()),
            "action_type": action_type,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.status = "waiting_approval"
        self._approval_event.clear()
        self._approval_decision = None

        # Wait up to 5 minutes for human decision
        try:
            await asyncio.wait_for(self._approval_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            self._approval_decision = "reject"

        self.pending_approval = None
        self.status = "running"
        return self._approval_decision or "reject"

    def resolve_approval(self, decision: str) -> None:
        self._approval_decision = decision
        self._approval_event.set()


# ── Schemas ────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    app_name: str
    task: str


class StartResponse(BaseModel):
    session_id: str
    status: str


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str
    app_name: str
    task: str
    steps_completed: int
    started_at: str
    result: Optional[dict] = None
    error: Optional[str] = None
    pending_approval: Optional[dict] = None


class ApprovalRequest(BaseModel):
    session_id: str
    approval_id: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/start", response_model=StartResponse)
async def start_session(
    req: StartRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Launch a new Computer Use session.
    The engine runs in the background and the client polls /sessions/{id} for status.
    """
    if not settings.computer_use_enabled:
        raise HTTPException(status_code=403, detail="Computer Use is disabled on this server")

    require_permission(user, "computer_use")

    session_id = str(uuid.uuid4())
    company = db.table("companies").select("*").eq("id", user["company_id"]).execute()
    if not company.data:
        raise HTTPException(status_code=404, detail="Company not found")
    company_config = company.data[0]

    state = _SessionState(session_id, req.app_name, req.task, user["company_id"])
    _sessions[session_id] = state

    # Run the CU engine in the background
    asyncio.create_task(_run_engine(state, company_config, db))

    log.info("CU session %s started: %s / %s", session_id, req.app_name, req.task)
    return StartResponse(session_id=session_id, status="running")


@router.get("/sessions/{session_id}", response_model=SessionStatusResponse)
async def get_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """Poll the status of a running Computer Use session."""
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if state.company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Not your session")

    return SessionStatusResponse(
        session_id=state.session_id,
        status=state.status,
        app_name=state.app_name,
        task=state.task,
        steps_completed=state.steps_completed,
        started_at=state.started_at,
        result=state.result,
        error=state.error,
        pending_approval=state.pending_approval,
    )


@router.post("/approve")
async def approve_action(
    req: ApprovalRequest,
    user: dict = Depends(get_current_user),
):
    """Approve a pending Computer Use action."""
    state = _sessions.get(req.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if state.company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Not your session")
    if state.status != "waiting_approval":
        raise HTTPException(status_code=400, detail="No pending approval for this session")
    if not state.pending_approval or state.pending_approval["id"] != req.approval_id:
        raise HTTPException(status_code=400, detail="Approval ID mismatch")

    state.resolve_approval("approve")
    log.info("CU session %s: action %s approved", req.session_id, req.approval_id)
    return {"status": "approved"}


@router.post("/reject")
async def reject_action(
    req: ApprovalRequest,
    user: dict = Depends(get_current_user),
):
    """Reject a pending Computer Use action (stops the session)."""
    state = _sessions.get(req.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if state.company_id != user["company_id"]:
        raise HTTPException(status_code=403, detail="Not your session")
    if state.status != "waiting_approval":
        raise HTTPException(status_code=400, detail="No pending approval for this session")

    state.resolve_approval("reject")
    log.info("CU session %s: action %s rejected — session will stop", req.session_id, req.approval_id if hasattr(req, "approval_id") else "?")
    return {"status": "rejected"}


# ── Background engine task ─────────────────────────────────────────────────────

async def _run_engine(state: "_SessionState", company_config: dict, db) -> None:
    try:
        from app.computer_use.engine import ComputerUseEngine
        engine = ComputerUseEngine(company_config)
        result = await engine.extract_data(
            app_name=state.app_name,
            task=state.task,
        )
        state.result = result
        state.status = "done"
        log.info("CU session %s completed successfully", state.session_id)

        # Persist result summary to action_log
        try:
            db.table("action_log").insert({
                "company_id": state.company_id,
                "session_id": state.session_id,
                "event": "computer_use_complete",
                "app_name": state.app_name,
                "task": state.task,
                "status": "success",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            log.warning("Could not write CU audit log: %s", exc)

    except Exception as exc:
        state.status = "error"
        state.error = str(exc)
        log.error("CU session %s failed: %s", state.session_id, exc)
