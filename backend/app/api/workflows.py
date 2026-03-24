"""Workflow API — view and manage workflow runs."""
import logging
from fastapi import APIRouter, Depends
from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.workflows.engine import WorkflowEngine
from app.workflows.state import get_workflow_progress

log = logging.getLogger(__name__)
router = APIRouter()


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
    engine = WorkflowEngine(db, user["company_id"])
    engine.fail(run_id, "Cancelled by user")
    return {"status": "cancelled"}
