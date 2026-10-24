"""Non-deploying orchestration contract for approved external actions.

The gateway must use the V11 PRIVATE SQL functions through a vetted internal
database transport. This module deliberately creates no Supabase REST RPC
shortcut and is not wired to the production approval endpoint.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


class DurableGateway(Protocol):
    async def enqueue(self, company_id: str, draft_id: str, key: str) -> str: ...
    async def claim(self, company_id: str, execution_id: str, worker_id: str) -> str | None: ...
    async def finish(
        self, company_id: str, execution_id: str, claim_token: str,
        outcome: str,
    ) -> bool: ...


class ExecutionReconciliationRequired(RuntimeError):
    """An external effect may have happened; never retry automatically."""


@dataclass(frozen=True)
class ExecutionReceipt:
    execution_id: str
    state: str
    claimed: bool


async def run_approved_external_action(
    *,
    gateway: DurableGateway,
    company_id: str,
    draft_id: str,
    idempotency_key: str,
    worker_id: str,
    external_action: Callable[[], Awaitable[None]],
) -> ExecutionReceipt:
    """Claims exactly once; losing workers never call the external system.

    If a send throws or recording the result fails, the caller must escalate
    reconciliation. An exception is NOT proof that the external effect failed.
    Only actions with a proven idempotent provider contract may be retried.
    """
    execution_id = await gateway.enqueue(company_id, draft_id, idempotency_key)
    claim_token = await gateway.claim(company_id, execution_id, worker_id)
    if not claim_token:
        return ExecutionReceipt(execution_id, "not_claimed", False)

    try:
        await external_action()
    except Exception as exc:
        try:
            await gateway.finish(company_id, execution_id, claim_token,
                                 "needs_reconciliation")
        except Exception:
            # The durable result is unknown; do not conceal the original
            # problem and, above all, do not dispatch a second time.
            pass
        raise ExecutionReconciliationRequired(
            "External outcome uncertain; manual reconciliation required"
        ) from exc

    try:
        finalized = await gateway.finish(
            company_id, execution_id, claim_token, "completed"
        )
    except Exception as exc:
        raise ExecutionReconciliationRequired(
            "Action may have completed but persistence failed"
        ) from exc
    if not finalized:
        raise ExecutionReconciliationRequired(
            "Action completed but claim was not accepted during finalization"
        )
    return ExecutionReceipt(execution_id, "completed", True)
