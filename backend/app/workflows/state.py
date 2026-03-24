"""Workflow state helpers — query active runs and progress info."""
from __future__ import annotations
from app.workflows.definitions import WORKFLOWS


def get_workflow_progress(run: dict) -> dict:
    """Return human-readable progress for a workflow run."""
    wf = WORKFLOWS.get(run.get("workflow_name", ""), {})
    steps = wf.get("steps", [])
    current = run.get("current_step", 0)
    total = run.get("total_steps", len(steps))
    current_step_name = steps[current]["name"] if current < len(steps) else "Complete"
    return {
        "run_id": run["id"],
        "workflow_name": run["workflow_name"],
        "status": run["status"],
        "current_step": current + 1,
        "total_steps": total,
        "current_step_name": current_step_name,
        "trigger_data": run.get("trigger_data", {}),
        "started_at": run.get("started_at"),
    }
