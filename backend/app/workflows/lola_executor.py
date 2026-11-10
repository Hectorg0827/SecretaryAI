"""
Executor for APPROVED Lola actions: customer email and QuickBooks purchase orders.

Nothing here runs unless the operator turns it on. Every gate is checked BEFORE
a job is claimed, so a disabled or mis-configured worker never takes a job and
never leaves it half-done:

    SECRETARYAI_LOLA_EXECUTOR_ENABLED=1            master switch (default off)

  Email (reuses the reviewed V15 delivery boundary):
    staging:  SECRETARYAI_DEPLOYMENT_ENV=staging + SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED=1
              + SECRETARYAI_STAGING_EMAIL_RECIPIENT=<one test mailbox>  (DB enforces the match)
    live:     SECRETARYAI_EMAIL_DELIVERY_MODE=live + SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED=1

  Purchase orders (QuickBooks Online only — it has an idempotent create):
    SECRETARYAI_PO_EXECUTION_MODE=sandbox          only an Intuit sandbox company
    SECRETARYAI_PO_EXECUTION_MODE=live + SECRETARYAI_LIVE_PO_CREATION_APPROVED=1

Outcome rules (shared with the V11 ledger):
  * provider error BEFORE any side effect could happen → ``failed``
  * anything uncertain                                 → ``needs_reconciliation``
                                                         (never retried automatically)
  * success                                            → ``completed`` WITH the provider's
                                                         id as ``external_reference`` — the
                                                         only thing Lola receipts accept as
                                                         "verified".
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.workflows.approved_execution import ExecutionReceipt, ExecutionReconciliationRequired
from app.workflows.email_provider_adapter import (
    AmbiguousEmailAcknowledgement,
    LiveEmailDeliveryDisabled,
    send_approved_email_via_provider,
)

log = logging.getLogger(__name__)


class ExecutorDisabled(RuntimeError):
    """The executor (or this action type's delivery) is not authorised to run."""


def executor_enabled() -> bool:
    return os.getenv("SECRETARYAI_LOLA_EXECUTOR_ENABLED") == "1"


def _require(cond: bool, reason: str) -> None:
    if not cond:
        raise ExecutorDisabled(reason)


def _tenant_matches(adapter: Any, company_id: str) -> bool:
    return str(getattr(adapter, "_config", {}).get("id") or "") == str(company_id)


async def _finish(gateway, company_id, execution_id, token, outcome, *, reference=None, error=None) -> bool:
    return await gateway.finish(company_id, execution_id, token, outcome,
                                external_reference=reference, error_code=error)


# ─── Email ───────────────────────────────────────────────────────────────────


def _email_mode() -> str:
    _require(executor_enabled(), "Lola executor is disabled")
    if os.getenv("SECRETARYAI_DEPLOYMENT_ENV", "").strip().lower() == "staging":
        _require(os.getenv("SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED") == "1", "Staging email pilot is disabled")
        allowed = os.getenv("SECRETARYAI_STAGING_EMAIL_RECIPIENT", "").strip()
        _require(bool(allowed) and not any(c in allowed for c in ",; \t\r\n"),
                 "Exactly one explicit staging test mailbox is required")
    # The provider boundary re-checks these; checking first means no claim is taken.
    _require(os.getenv("SECRETARYAI_EMAIL_DELIVERY_MODE") == "live"
             and os.getenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED") == "1",
             "Approved-email delivery has not been authorised")
    return "staging" if os.getenv("SECRETARYAI_DEPLOYMENT_ENV", "").strip().lower() == "staging" else "live"


async def execute_approved_email(*, gateway, adapter, company_id: str, execution_id: str,
                                 worker_id: str) -> ExecutionReceipt:
    mode = _email_mode()
    _require(adapter is not None and _tenant_matches(adapter, company_id), "Mail connector tenant mismatch")
    if mode == "staging":
        claim = await gateway.claim_staging_email(company_id, execution_id, worker_id,
                                                  os.getenv("SECRETARYAI_STAGING_EMAIL_RECIPIENT", "").strip())
    else:
        claim = await gateway.claim_email(company_id, execution_id, worker_id)
    if not claim.get("claimed"):
        return ExecutionReceipt(execution_id, "not_claimed", False)
    token = claim["claim_token"]

    try:
        sent = await send_approved_email_via_provider(adapter, to=claim["recipient"], subject=claim["subject"],
                                                      body=claim["body"])
    except (LiveEmailDeliveryDisabled, ValueError):
        # Raised before the provider was called: nothing was sent.
        await _finish(gateway, company_id, execution_id, token, "failed", error="delivery_not_attempted")
        return ExecutionReceipt(execution_id, "failed", True)
    except (AmbiguousEmailAcknowledgement, Exception) as exc:
        try:
            await _finish(gateway, company_id, execution_id, token, "needs_reconciliation",
                          error="provider_outcome_unknown")
        except Exception:
            pass
        raise ExecutionReconciliationRequired("Email outcome uncertain; do not resend automatically") from exc

    message_id = str(sent.get("provider_message_id") or "").strip()
    if not message_id:
        await _finish(gateway, company_id, execution_id, token, "needs_reconciliation",
                      error="missing_provider_reference")
        raise ExecutionReconciliationRequired("Provider accepted mail without a message id")
    try:
        ok = await _finish(gateway, company_id, execution_id, token, "completed",
                           reference=f"email:{message_id}"[:500])
    except Exception as exc:
        raise ExecutionReconciliationRequired("Mail accepted but the ledger is unavailable") from exc
    if not ok:
        raise ExecutionReconciliationRequired("Mail accepted but the claim was no longer valid")
    return ExecutionReceipt(execution_id, "completed", True)


# ─── Purchase orders ─────────────────────────────────────────────────────────


def _po_mode(adapter: Any, company_id: str) -> str:
    _require(executor_enabled(), "Lola executor is disabled")
    mode = os.getenv("SECRETARYAI_PO_EXECUTION_MODE", "").strip().lower()
    _require(mode in ("sandbox", "live"), "Purchase order execution is disabled")
    _require(adapter is not None and _tenant_matches(adapter, company_id), "QuickBooks connector tenant mismatch")
    target, is_sandbox = adapter.po_execution_target()
    _require(target == "qbo", "Automated purchase orders require QuickBooks Online (idempotent create)")
    if mode == "sandbox":
        _require(is_sandbox is True, "Sandbox mode refuses a live QuickBooks company")
    else:
        _require(os.getenv("SECRETARYAI_LIVE_PO_CREATION_APPROVED") == "1",
                 "Live purchase order creation has not been authorised")
    return mode


def _is_definite_rejection(exc: Exception) -> bool:
    """A 4xx validation answer from Intuit means the PO was NOT created."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return isinstance(code, int) and 400 <= code < 500 and code not in (408, 409, 429)


async def execute_approved_purchase_order(*, gateway, adapter, company_id: str, execution_id: str,
                                          worker_id: str) -> ExecutionReceipt:
    _po_mode(adapter, company_id)
    claim = await gateway.claim_purchase_order(company_id, execution_id, worker_id)
    if not claim.get("claimed"):
        return ExecutionReceipt(execution_id, "not_claimed", False)
    token = claim["claim_token"]
    lines = [{"item_id": str(l["item_id"]), "quantity": float(l["quantity"]),
              "unit_cost": float(l["unit_cost"]), "description": str(l.get("description") or "")[:200]}
             for l in claim["lines"]]
    try:
        po = await adapter.create_purchase_order_idempotent(
            vendor_id=str(claim["vendor_id"]), line_items=lines, memo=str(claim.get("memo") or "")[:1000],
            request_id=f"lola-{execution_id}"[:50],
        )
    except Exception as exc:
        if _is_definite_rejection(exc):
            await _finish(gateway, company_id, execution_id, token, "failed", error="provider_rejected")
            return ExecutionReceipt(execution_id, "failed", True)
        try:
            await _finish(gateway, company_id, execution_id, token, "needs_reconciliation",
                          error="provider_outcome_unknown")
        except Exception:
            pass
        raise ExecutionReconciliationRequired("Purchase order outcome uncertain; check QuickBooks") from exc

    qb_id = str(getattr(po, "qb_id", "") or "").strip()
    if not qb_id:
        await _finish(gateway, company_id, execution_id, token, "needs_reconciliation",
                      error="missing_provider_reference")
        raise ExecutionReconciliationRequired("QuickBooks returned no purchase order id")
    try:
        ok = await _finish(gateway, company_id, execution_id, token, "completed", reference=f"qbo:po:{qb_id}")
    except Exception as exc:
        raise ExecutionReconciliationRequired("PO created but the ledger is unavailable") from exc
    if not ok:
        raise ExecutionReconciliationRequired("PO created but the claim was no longer valid")
    return ExecutionReceipt(execution_id, "completed", True)


EXECUTORS = {
    "email_reply": execute_approved_email,
    "purchase_order": execute_approved_purchase_order,
}


async def execute_approved_action(action_type: str, **kwargs) -> ExecutionReceipt:
    fn = EXECUTORS.get(action_type)
    _require(fn is not None, f"No executor for action type {action_type!r}")
    return await fn(**kwargs)
