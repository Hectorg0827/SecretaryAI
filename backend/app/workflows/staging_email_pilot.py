"""Opt-in staging-mailbox pilot. Not scheduled or deployed by default.

Only an explicitly allowlisted email can be claimed, and the database enforces
the match before changing queued -> running. No customer broadcasts or retries.
"""
import os
from typing import Any

from app.workflows.approved_execution import (
    ExecutionReceipt, ExecutionReconciliationRequired,
)
from app.workflows.email_provider_adapter import send_approved_email_via_provider
from app.workflows.execution_gateway import PostgresDurableGateway


class StagingPilotDisabled(RuntimeError):
    pass


async def run_staging_email_pilot(
    *, gateway: PostgresDurableGateway,
    adapter: Any, company_id: str, execution_id: str, worker_id: str,
) -> ExecutionReceipt:
    if (os.getenv("SECRETARYAI_DEPLOYMENT_ENV") != "staging"
            or os.getenv("SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED") != "1"):
        raise StagingPilotDisabled("Email staging pilot is disabled")
    allowed = os.getenv("SECRETARYAI_STAGING_EMAIL_RECIPIENT", "").strip()
    if not allowed or any(ch in allowed for ch in ",; \t\r\n"):
        raise StagingPilotDisabled("Exactly one explicit test mailbox is required")

    # The private DB function enforces this same allowlist before any claim.
    claim = await gateway.claim_staging_email(
        company_id, execution_id, worker_id, allowed
    )
    if not claim["claimed"]:
        return ExecutionReceipt(execution_id, "not_claimed", False)
    token = claim["claim_token"]

    try:
        await send_approved_email_via_provider(
            adapter, to=claim["recipient"],
            subject=claim["subject"], body=claim["body"],
        )
    except Exception as exc:
        try:
            await gateway.finish(
                company_id, execution_id, token, "needs_reconciliation"
            )
        except Exception:
            pass
        raise ExecutionReconciliationRequired(
            "Staging mail outcome uncertain; do not retry automatically"
        ) from exc

    try:
        finished = await gateway.finish(
            company_id, execution_id, token, "completed"
        )
    except Exception as exc:
        raise ExecutionReconciliationRequired(
            "Provider accepted mail but finalization failed"
        ) from exc
    if not finished:
        raise ExecutionReconciliationRequired(
            "Provider accepted mail but the claim is no longer valid"
        )
    return ExecutionReceipt(execution_id, "completed", True)
