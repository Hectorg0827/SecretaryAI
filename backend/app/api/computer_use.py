"""Computer-Use sessions persisted in Redis for cross-worker polling and approval.

Session records have a one-hour TTL. Redis errors fail closed (503) instead
of returning an incorrect 404 or accepting an unverified approval.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()
_SESSION_TTL_SECONDS = 3600
_APPROVAL_TIMEOUT_SECONDS = 300


def _key(session_id: str) -> str:
    return f"cu:session:{session_id}"


def _redis():
    import redis.asyncio as redis_lib
    return redis_lib.from_url(
        settings.redis_url, decode_responses=True,
        socket_connect_timeout=2, socket_timeout=2,
    )


async def _read(session_id: str) -> dict:
    try:
        async with _redis() as r:
            state = await r.hgetall(_key(session_id))
        return state or {}
    except Exception:
        log.exception("CU Redis read unavailable")
        raise HTTPException(status_code=503, detail="Session store unavailable")


async def _write(session_id: str, **fields) -> None:
    try:
        async with _redis() as r:
            await r.hset(_key(session_id), mapping=fields)
            await r.expire(_key(session_id), _SESSION_TTL_SECONDS)
    except Exception:
        log.exception("CU Redis write unavailable")
        raise HTTPException(status_code=503, detail="Session store unavailable")


# Atomic compare-and-set: approval must still belong to the expected company,
# pending action, and session. Only the first decision can win.
_DECIDE_SCRIPT = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then return 'missing' end
if redis.call('HGET', key, 'company_id') ~= ARGV[1] then return 'forbidden' end
if redis.call('HGET', key, 'status') ~= 'waiting_approval' then return 'not_pending' end
if redis.call('HGET', key, 'approval_id') ~= ARGV[2] then return 'mismatch' end
if redis.call('HGET', key, 'approval_decision') ~= '' then return 'already' end
redis.call('HSET', key, 'approval_decision', ARGV[3])
return 'ok'
"""


class _SessionState:
    def __init__(self, session_id: str, app_name: str, task: str, company_id: str):
        self.session_id = session_id
        self.app_name = app_name
        self.task = task
        self.company_id = company_id
        self.started_at = datetime.now(timezone.utc).isoformat()

    async def save(self) -> None:
        await _write(
            self.session_id, session_id=self.session_id, app_name=self.app_name,
            task=self.task, company_id=self.company_id, started_at=self.started_at,
            status="running", steps_completed="0", result="", error="",
            pending_approval="", approval_id="", approval_decision="",
        )

    async def request_approval(self, action_type: str, description: str) -> str:
        """Wait for an owner/manager's decision, including on another worker."""
        approval_id = str(uuid.uuid4())
        pending = {
            "id": approval_id, "action_type": action_type,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await _write(
            self.session_id, status="waiting_approval",
            pending_approval=json.dumps(pending), approval_id=approval_id,
            approval_decision="",
        )
        decision = "reject"
        try:
            # Poll Redis, not a per-process asyncio.Event.
            for _ in range(_APPROVAL_TIMEOUT_SECONDS * 4):
                async with _redis() as r:
                    value = await r.hget(_key(self.session_id), "approval_decision")
                if value in ("approve", "reject"):
                    decision = value
                    break
                if value is None:  # expired or deleted; never auto-approve
                    break
                await asyncio.sleep(0.25)
        except Exception:
            log.exception("CU approval store unavailable; rejecting action")
        finally:
            try:
                await _write(
                    self.session_id, status="running", pending_approval="",
                    approval_id="", approval_decision="",
                )
            except HTTPException:
                pass
        return decision


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


@router.post("/start", response_model=StartResponse)
async def start_session(
    req: StartRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    if not settings.computer_use_enabled:
        raise HTTPException(status_code=403, detail="Computer Use is disabled on this server")
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Not authorized to start a Computer Use session")

    company = db.table("companies").select("*").eq("id", user["company_id"]).execute()
    if not company.data:
        raise HTTPException(status_code=404, detail="Company not found")

    state = _SessionState(str(uuid.uuid4()), req.app_name, req.task, user["company_id"])
    # Do not start work without a durable session that another worker can read.
    await state.save()
    asyncio.create_task(_run_engine(state, company.data[0], db))
    log.info("CU session %s started", state.session_id)
    return StartResponse(session_id=state.session_id, status="running")


async def _owned_session(session_id: str, user: dict) -> dict:
    state = await _read(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if state["company_id"] != user["company_id"]:
        raise HTTPException(status_code=403, detail="Not your session")
    return state


@router.get("/sessions/{session_id}", response_model=SessionStatusResponse)
async def get_session(session_id: str, user: dict = Depends(get_current_user)):
    state = await _owned_session(session_id, user)
    return SessionStatusResponse(
        session_id=state["session_id"], status=state["status"],
        app_name=state["app_name"], task=state["task"],
        steps_completed=int(state.get("steps_completed") or 0),
        started_at=state["started_at"],
        result=json.loads(state["result"]) if state.get("result") else None,
        error=state.get("error") or None,
        pending_approval=json.loads(state["pending_approval"])
        if state.get("pending_approval") else None,
    )


async def _decide(req: ApprovalRequest, user: dict, decision: str) -> dict:
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Not authorized to decide approvals")
    await _owned_session(req.session_id, user)
    try:
        async with _redis() as r:
            outcome = await r.eval(
                _DECIDE_SCRIPT, 1, _key(req.session_id),
                user["company_id"], req.approval_id, decision,
            )
    except Exception:
        log.exception("CU Redis decision unavailable")
        raise HTTPException(status_code=503, detail="Session store unavailable")
    errors = {
        "missing": (404, "Session not found"),
        "forbidden": (403, "Not your session"),
        "not_pending": (400, "No pending approval for this session"),
        "mismatch": (400, "Approval ID mismatch"),
        "already": (409, "Approval already decided"),
    }
    if outcome != "ok":
        status, detail = errors.get(outcome, (503, "Session store unavailable"))
        raise HTTPException(status_code=status, detail=detail)
    log.info("CU session %s decision %s", req.session_id, decision)
    return {"status": "approved" if decision == "approve" else "rejected"}


@router.post("/approve")
async def approve_action(req: ApprovalRequest, user: dict = Depends(get_current_user)):
    return await _decide(req, user, "approve")


@router.post("/reject")
async def reject_action(req: ApprovalRequest, user: dict = Depends(get_current_user)):
    return await _decide(req, user, "reject")


async def _run_engine(state: _SessionState, company_config: dict, db) -> None:
    try:
        from app.computer_use.engine import ComputerUseEngine
        engine = ComputerUseEngine(company_config)
        result = await engine.extract_data(app_name=state.app_name, task=state.task)
        await _write(state.session_id, status="done", result=json.dumps(result))
        log.info("CU session %s completed successfully", state.session_id)
        try:
            db.table("action_log").insert({
                "company_id": state.company_id, "session_id": state.session_id,
                "event": "computer_use_complete", "app_name": state.app_name,
                "task": state.task, "status": "success",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            log.warning("Could not write CU audit log: %s", exc)
    except Exception as exc:
        log.exception("CU session %s failed", state.session_id)
        try:
            await _write(state.session_id, status="error", error=str(exc)[:500])
        except HTTPException:
            log.error("Cannot persist CU failure; Redis unavailable")
