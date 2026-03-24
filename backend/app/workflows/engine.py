"""
Workflow Engine — orchestrates multi-step automated workflows.

Usage:
    engine = WorkflowEngine(db, company_id)
    run_id = engine.start("low_stock_reorder", trigger_data={"item_id": "...", "product_name": "..."})
    engine.advance(run_id, step_result={"draft_id": "..."})
    engine.complete(run_id)
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.workflows.definitions import WORKFLOWS

log = logging.getLogger(__name__)


class WorkflowEngine:
    def __init__(self, db, company_id: str):
        self._db = db
        self._company_id = company_id

    def start(self, workflow_name: str, trigger_data: dict) -> str:
        """Create a new workflow run. Returns the run ID."""
        wf = WORKFLOWS.get(workflow_name)
        if not wf:
            raise ValueError(f"Unknown workflow: {workflow_name}")

        run_id = str(uuid.uuid4())
        self._db.table("workflow_runs").insert({
            "id": run_id,
            "company_id": self._company_id,
            "workflow_name": workflow_name,
            "trigger_type": wf["trigger_type"],
            "trigger_data": trigger_data,
            "current_step": 0,
            "total_steps": len(wf["steps"]),
            "status": "running",
            "step_results": [],
        }).execute()
        log.info("Started workflow %s (run %s) for company %s", workflow_name, run_id, self._company_id)
        return run_id

    def advance(self, run_id: str, step_result: dict, awaiting_entity_id: Optional[str] = None) -> None:
        """Record a step result and advance to next step."""
        run = self._get_run(run_id)
        if not run:
            return

        results = list(run.get("step_results") or [])
        results.append({
            "step": run["current_step"] + 1,
            "result": step_result,
            "at": datetime.now(timezone.utc).isoformat(),
        })

        next_step = run["current_step"] + 1
        total = run["total_steps"]

        if next_step >= total:
            status = "completed"
        elif awaiting_entity_id:
            status = "awaiting_approval"
        else:
            status = "running"

        update = {
            "current_step": next_step,
            "step_results": results,
            "status": status,
        }
        if awaiting_entity_id:
            update["awaiting_entity_id"] = awaiting_entity_id
        if status == "completed":
            update["completed_at"] = datetime.now(timezone.utc).isoformat()

        self._db.table("workflow_runs").update(update).eq("id", run_id).execute()

    def resume_after_approval(self, run_id: str, approved: bool) -> None:
        """Called when a pending approval resolves."""
        if not approved:
            self._db.table("workflow_runs").update({
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
            return
        run = self._get_run(run_id)
        if not run:
            return
        next_step = run["current_step"] + 1
        total = run["total_steps"]
        status = "completed" if next_step >= total else "running"
        update = {"current_step": next_step, "status": status, "awaiting_entity_id": None}
        if status == "completed":
            update["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._db.table("workflow_runs").update(update).eq("id", run_id).execute()

    def fail(self, run_id: str, error: str) -> None:
        self._db.table("workflow_runs").update({
            "status": "failed",
            "step_results": self._get_run(run_id).get("step_results", []) + [{"error": error}],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).execute()

    def _get_run(self, run_id: str) -> Optional[dict]:
        result = self._db.table("workflow_runs").select("*").eq("id", run_id).maybe_single().execute()
        return result.data

    def list_active(self) -> list[dict]:
        result = (
            self._db.table("workflow_runs")
            .select("*")
            .eq("company_id", self._company_id)
            .in_("status", ["running", "awaiting_approval"])
            .order("started_at", desc=True)
            .limit(20)
            .execute()
        )
        return result.data or []
