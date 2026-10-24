"""V15 email execution certification: synthetic delivery ONLY.

No mail provider is imported here, and production cannot enable this worker.
An actual delivery adapter must be separately reviewed and released.
"""
import os
from typing import Awaitable, Callable

from app.workflows.approved_execution import (
    ExecutionReceipt, ExecutionReconciliationRequired,
)
from app.workflows.execution_gateway import PostgresDurableGateway


class EmailWorkerDisabled(RuntimeError):
    pass


async def simulate_approved_email(
    *,
    gateway: PostgresDurableGateway,
    company_id: str,
    execution_id: str,
    worker_id: str,
    synthetic_delivery: Callable[[str, str, str], Awaitable[None]],
) -> ExecutionReceipt:
    """Test-only worker: claim immutable payload, simulate send, then finalize."""
    if (os.getenv("SECRETARYAI_EMAIL_WORKER_MODE") != "simulate"
            or os.getenv("SECRETARYAI_TEST_DATABASE_ONLY") != "1"):
        raise EmailWorkerDisabled("Email worker only available in disposable CI simulation")

    claim = await gateway.claim_email(company_id, execution_id, worker_id)
    if not claim["claimed"]:
        return ExecutionReceipt(execution_id, "not_claimed", False)
    token = claim["claim_token"]

    try:
        await synthetic_delivery(
            claim["recipient"], claim["subject"], claim["body"]
        )
    except Exception as exc:
        try:
            await gateway.finish(
                company_id, execution_id, token, "needs_reconciliation"
            )
        except Exception:
            pass
        raise ExecutionReconciliationRequired(
            "Synthetic delivery outcome uncertain"
        ) from exc

    try:
        persisted = await gateway.finish(
            company_id, execution_id, token, "completed"
        )
    except Exception as exc:
        raise ExecutionReconciliationRequired(
            "Synthetic delivery completed but the ledger is unavailable"
        ) from exc
    if not persisted:
        raise ExecutionReconciliationRequired(
            "Synthetic delivery completed but claim finalization was rejected"
        )
    return ExecutionReceipt(execution_id, "completed", True)
