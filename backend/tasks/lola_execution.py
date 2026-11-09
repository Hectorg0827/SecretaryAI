"""
Celery task: execute ONE approved Lola action (email or purchase order).

Dispatched by the approval endpoint right after the database atomically
approved and queued the action — only when SECRETARYAI_LOLA_EXECUTOR_ENABLED=1.
Never retried automatically: an uncertain outcome is left for a human to
reconcile (see app/workflows/lola_executor.py).
"""
import asyncio
import logging

from celery_app import app
from tasks.base import build_adapter, get_supabase

log = logging.getLogger(__name__)


@app.task(name="tasks.lola_execution.execute", bind=True, max_retries=0, acks_late=False)
def execute(self, company_id: str, execution_id: str, action_type: str):
    from app.workflows.execution_gateway import ExecutionDatabaseUnavailable, PostgresDurableGateway
    from app.workflows.approved_execution import ExecutionReconciliationRequired
    from app.workflows.lola_executor import ExecutorDisabled, execute_approved_action, executor_enabled

    if not executor_enabled():
        return {"execution_id": execution_id, "state": "skipped_disabled"}
    try:
        db = get_supabase()
        rows = db.table("companies").select("*").eq("id", company_id).limit(2).execute().data or []
        if len(rows) != 1:
            return {"execution_id": execution_id, "state": "skipped_company_missing"}
        adapter = build_adapter(rows[0])
        gateway = PostgresDurableGateway.from_environment()
        receipt = asyncio.run(execute_approved_action(
            action_type, gateway=gateway, adapter=adapter, company_id=company_id,
            execution_id=execution_id, worker_id=f"celery:{self.request.id}"[:120],
        ))
        return {"execution_id": execution_id, "state": receipt.state}
    except ExecutorDisabled as exc:
        log.warning("Lola execution %s not started: %s", execution_id, exc)
        return {"execution_id": execution_id, "state": "skipped_disabled"}
    except ExecutionReconciliationRequired:
        log.error("Lola execution %s needs reconciliation", execution_id)
        return {"execution_id": execution_id, "state": "needs_reconciliation"}
    except ExecutionDatabaseUnavailable:
        log.error("Lola execution %s: execution database unavailable", execution_id)
        return {"execution_id": execution_id, "state": "execution_db_unavailable"}
