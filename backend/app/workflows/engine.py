"""
Workflow Engine — orchestrates multi-step automated workflows.

Usage:
    engine = WorkflowEngine(db, company_id)
    run_id = engine.start("low_stock_reorder", trigger_data={"item_id": "...", "product_name": "..."})

    # Before executing a step, check policy:
    decision = await engine.evaluate_step_policy(run_id, requested_by="system")
    if decision and decision.effect != "allow":
        engine.pause_for_policy(run_id, decision)
        return  # caller handles approval flow

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

    def start(self, workflow_name: str, trigger_data: dict, correlation_id: Optional[str] = None) -> str:
        """Create a new workflow run. Returns the run ID."""
        wf = WORKFLOWS.get(workflow_name)
        if not wf:
            raise ValueError(f"Unknown workflow: {workflow_name}")

        run_id = str(uuid.uuid4())
        row: dict = {
            "id": run_id,
            "company_id": self._company_id,
            "workflow_name": workflow_name,
            "trigger_type": wf["trigger_type"],
            "trigger_data": trigger_data,
            "current_step": 0,
            "total_steps": len(wf["steps"]),
            "status": "running",
            "step_results": [],
        }
        if correlation_id:
            row["correlation_id"] = correlation_id
        self._db.table("workflow_runs").insert(row).execute()
        log.info("Started workflow %s (run %s) for company %s", workflow_name, run_id, self._company_id)
        return run_id

    def get_current_step_def(self, run_id: str) -> Optional[dict]:
        """Return the definition dict for the current step, or None if finished."""
        run = self._get_run(run_id)
        if not run:
            return None
        wf = WORKFLOWS.get(run["workflow_name"])
        if not wf:
            return None
        step_index = run.get("current_step", 0)
        steps = wf["steps"]
        if step_index >= len(steps):
            return None
        return steps[step_index]

    async def evaluate_step_policy(
        self,
        run_id: str,
        requested_by: str = "system",
        amount=None,
    ):
        """
        Evaluate PolicyEngine for the current step.
        Returns ActionDecision if the step has action_class commit/destructive,
        or None for read/draft steps (always allowed without policy check).
        """
        step_def = self.get_current_step_def(run_id)
        if not step_def:
            return None

        action_class_str = step_def.get("action_class", "read")
        if action_class_str in ("read", "draft"):
            return None  # No policy check needed

        run = self._get_run(run_id)
        if not run:
            return None

        from app.domain.policy import PolicyEngine
        from app.domain.contracts import ActionProposal, ActionClass

        _class_map = {
            "commit": ActionClass.COMMIT,
            "destructive": ActionClass.DESTRUCTIVE,
        }
        action_class = _class_map.get(action_class_str, ActionClass.COMMIT)

        proposal = ActionProposal(
            action_type=step_def.get("action_type", "workflow_step"),
            action_class=action_class,
            description=step_def.get("description", ""),
            company_id=self._company_id,
            requested_by=requested_by,
            amount=amount,
        )

        engine = PolicyEngine(company_id=self._company_id, db=self._db)
        return await engine.evaluate(proposal)

    def pause_for_policy(self, run_id: str, decision) -> None:
        """
        Pause a workflow run pending approval after a policy require_approval decision.
        Stores approval_roles and policy_rule_id on the run row.
        """
        update: dict = {
            "status": "awaiting_approval",
            "awaiting_entity_id": None,  # cleared; approval token stored separately
        }
        if hasattr(decision, "rule_id") and decision.rule_id:
            update["policy_rule_id"] = decision.rule_id
        if hasattr(decision, "approval_roles") and decision.approval_roles:
            # Store as metadata in step_results
            run = self._get_run(run_id)
            if run:
                results = list(run.get("step_results") or [])
                results.append({
                    "step": run.get("current_step", 0),
                    "policy_paused": True,
                    "approval_roles": decision.approval_roles,
                    "rule_name": decision.rule_name,
                    "at": datetime.now(timezone.utc).isoformat(),
                })
                update["step_results"] = results
        self._db.table("workflow_runs").update(update).eq("id", run_id).execute()
        log.info("Workflow run %s paused for policy approval (rule=%s)", run_id, getattr(decision, "rule_name", "?"))

    def advance(self, run_id: str, step_result: dict, awaiting_entity_id: Optional[str] = None) -> None:
        """Record a step result and advance to the next step."""
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

    def resume_after_approval(self, run_id: str, approved: bool, approved_by: Optional[str] = None) -> None:
        """Called when a pending approval resolves."""
        if not approved:
            self._db.table("workflow_runs").update({
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).eq("company_id", self._company_id).execute()
            return
        run = self._get_run(run_id)
        if not run:
            return
        next_step = run["current_step"] + 1
        total = run["total_steps"]
        status = "completed" if next_step >= total else "running"
        update: dict = {
            "current_step": next_step,
            "status": status,
            "awaiting_entity_id": None,
            "policy_rule_id": None,
        }
        if approved_by:
            update["approved_by"] = approved_by
            update["approved_at"] = datetime.now(timezone.utc).isoformat()
        if status == "completed":
            update["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._db.table("workflow_runs").update(update).eq("id", run_id).execute()

    def fail(self, run_id: str, error: str) -> None:
        run = self._get_run(run_id)
        results = (run.get("step_results", []) if run else []) + [{"error": error}]
        # Scope the write to this tenant so one company can't fail another's run.
        self._db.table("workflow_runs").update({
            "status": "failed",
            "step_results": results,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).eq("company_id", self._company_id).execute()

    def _get_run(self, run_id: str) -> Optional[dict]:
        # Tenant-scoped: a run belonging to another company is invisible.
        result = (
            self._db.table("workflow_runs").select("*")
            .eq("id", run_id).eq("company_id", self._company_id)
            .maybe_single().execute()
        )
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
