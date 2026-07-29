"""Workflow API — view and manage workflow runs."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.workflows.engine import WorkflowEngine
from app.workflows.state import get_workflow_progress

log = logging.getLogger(__name__)
router = APIRouter()


def _audit(db, company_id: str, user_id: str, action: str, detail: dict) -> None:
    """Write a row to action_log for workflow audit trail. Best-effort — never raises."""
    try:
        db.table("action_log").insert({
            "company_id":  company_id,
            "user_id":     user_id,
            "action_type": action,
            "content":     detail,
            "status":      "completed",
        }).execute()
    except Exception as exc:
        log.warning("Workflow audit log write failed: %s", exc)


@router.get("/")
async def list_workflows(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List active and recent workflow runs for this company."""
    engine = WorkflowEngine(db, user["company_id"])
    runs = engine.list_active()
    return {"workflows": [get_workflow_progress(r) for r in runs]}


@router.post("/{run_id}/cancel")
async def cancel_workflow(
    run_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    # Ownership check — a run belonging to another tenant must 404 (IDOR fix).
    owned = (
        db.table("workflow_runs").select("id")
        .eq("id", run_id).eq("company_id", user["company_id"]).execute()
    )
    if not owned.data:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    engine = WorkflowEngine(db, user["company_id"])
    engine.fail(run_id, "Cancelled by user")
    _audit(db, user["company_id"], user["sub"], "workflow_cancel", {"run_id": run_id})
    log.info("Workflow %s cancelled by user %s", run_id, user["sub"])
    return {"status": "cancelled"}
