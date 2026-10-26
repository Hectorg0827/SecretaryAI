"""
Action approval endpoints — manage the DRAFT_AND_WAIT queue.
After approval, actions are executed immediately for email_reply type
and dispatched as Celery tasks for heavier operations.
"""
import logging
import os
import uuid
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_db, get_adapter
from app.auth.rbac import get_current_user, require_permission
from app.actions.approval_queue import ApprovalQueue
from app.domain.contracts import ActionClass, ActionProposal
from app.domain.policy import PolicyEngine
from app.utils.audit import write_audit_event
from app.workflows.execution_gateway import (
    ExecutionDatabaseUnavailable, PostgresDurableGateway,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _content_amount(content: dict) -> Optional[Decimal]:
    """Best-effort monetary amount from a draft's content for policy checks."""
    for key in ("amount", "total", "total_amount", "value"):
        v = content.get(key)
        if v is not None:
            try:
                return Decimal(str(v))
            except (InvalidOperation, ValueError, TypeError):
                return None
    return None


async def _execution_policy_effect(db, company_id: str, requested_by: str,
                                   draft: dict, content: dict) -> str:
    """
    Evaluate the tenant PolicyEngine for a COMMIT action about to execute.
    Returns the effect ('allow' | 'require_approval' | 'deny').

    On policy evaluation failure, fail CLOSED ('deny'). Human approval cannot
    override an unavailable policy system or a company hard limit.
    """
    try:
        proposal = ActionProposal(
            action_type=draft.get("action_type", "") or "unknown",
            action_class=ActionClass.COMMIT,
            description=f"Execute approved draft {draft.get('id')}",
            company_id=company_id,
            requested_by=requested_by,
            amount=_content_amount(content),
        )
        decision = await PolicyEngine(company_id, db).evaluate(proposal)
        return decision.effect
    except Exception as exc:
        log.error("Policy evaluation errored for draft %s (failing closed): %s",
                  draft.get("id"), exc)
        return "deny"


async def _execute_approved_draft(draft: dict, adapter) -> str:
    """Attempt an approved action; report its actual execution stage.

    A human approval is not proof that an external side effect succeeded.
    Never automatically retry an uncertain email send or PO dispatch here.
    """
    action_type = draft.get("action_type", "")
    content = draft.get("content", {}) or {}

    if action_type == "email_reply":
        to = content.get("to", "")
        body = content.get("body", "")
        if not to or not body:
            log.error("Approved email draft %s is missing recipient or body", draft.get("id"))
            return "failed"
        try:
            receipt = await adapter.send_email_reply(
                to=to, subject=content.get("subject", ""), body=body
            )
            if not isinstance(receipt, dict) or receipt.get("status") != "sent":
                log.error("Approved email draft %s has uncertain provider acknowledgment",
                          draft.get("id"))
                return "needs_reconciliation"
            log.info("Provider accepted approved email draft %s", draft.get("id"))
            return "sent"
        except Exception:
            log.exception("Approved email draft %s send outcome uncertain", draft.get("id"))
            return "needs_reconciliation"

    if action_type == "purchase_order":
        try:
            from celery_app import celery_app
            celery_app.send_task(
                "tasks.actions.process_approved_po",
                kwargs={"draft_id": draft.get("id"), "content": content},
            )
            log.info("Dispatched approved PO draft %s", draft.get("id"))
            return "dispatched"
        except Exception:
            log.exception("Approved PO draft %s dispatch outcome uncertain", draft.get("id"))
            return "needs_reconciliation"

    log.warning("No executor for approved action type %s", action_type)
    return "unsupported"


@router.get("/pending")
async def list_pending_actions(
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    """List all drafts awaiting approval for this company."""
    company_id = user["company_id"]
    queue = ApprovalQueue(db)
    drafts = await queue.get_pending(company_id)
    return {"drafts": drafts, "company_id": company_id}


class ApprovalRequest(BaseModel):
    edited_content: Optional[dict] = None


class RejectionRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/approve/{draft_id}")
async def approve_draft(
    draft_id: str,
    body: ApprovalRequest,
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
    adapter=Depends(get_adapter),
):
    """Approve a pending draft action and execute it."""
    queue = ApprovalQueue(db)

    # Fetch the draft — SCOPED TO THE CALLER'S COMPANY. A draft belonging to
    # another tenant must be invisible here, so cross-company approval/execution
    # is impossible (IDOR fix).
    try:
        draft_result = (
            db.table("drafts").select("*")
            .eq("id", draft_id).eq("company_id", user["company_id"]).execute()
        )
        draft = draft_result.data[0] if draft_result.data else {}
    except Exception:
        draft = {}

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Idempotency: only a still-pending draft may be approved/executed. A second
    # approval must NOT re-send the email / re-dispatch the PO.
    if draft.get("status") != "pending":
        return {"draft_id": draft_id, "status": draft.get("status"), "already_processed": True}

    # Policy gate: enforce the tenant PolicyEngine BEFORE approving/executing, so
    # a human approval can't push an action past a hard ceiling (e.g. an amount
    # over the company's limit). Blocked → 403, draft stays pending.
    content_for_policy = body.edited_content or draft.get("content", {}) or {}
    if await _execution_policy_effect(db, user["company_id"], user["sub"], draft, content_for_policy) == "deny":
        raise HTTPException(status_code=403, detail="This action is blocked by your company's action policy")

    # Dark-launch durable mode. One PostgreSQL transaction must approve and
    # enqueue together; never approve via PostgREST and enqueue afterward.
    # This mode queues approved email only. No live send or worker dispatch.
    if os.getenv("SECRETARYAI_DURABLE_APPROVAL_ENABLED") == "1":
        if draft.get("action_type") != "email_reply":
            raise HTTPException(
                status_code=409,
                detail="Durable approval is not enabled for this action type",
            )
        try:
            gateway = PostgresDurableGateway.from_environment()
            durable = await gateway.approve_and_enqueue(
                company_id=user["company_id"],
                draft_id=draft_id,
                actor_id=user["sub"],
                expected_content=draft.get("content", {}) or {},
                idempotency_key=f"approved-draft:{draft_id}",
                edited_content=body.edited_content,
            )
        except ExecutionDatabaseUnavailable as exc:
            log.error("Durable approval unavailable for draft %s", draft_id)
            raise HTTPException(
                status_code=503, detail="Durable approval unavailable; action not sent"
            ) from exc
        except Exception as exc:
            log.exception("Durable approval rejected for draft %s", draft_id)
            raise HTTPException(
                status_code=409, detail="Draft could not be durably approved"
            ) from exc

        write_audit_event(
            db, company_id=user["company_id"],
            event_type="draft_approved", actor_id=user["sub"],
            approved_by=user["sub"], action_class=draft.get("action_type"),
            metadata={"draft_id": draft_id, "action_type": "email_reply",
                      "execution_status": "queued",
                      "execution_id": durable["execution_id"]},
        )
        return {
            "draft_id": draft_id,
            "status": durable.get("status", "approved"),
            "execution_status": "queued",
            "execution_id": durable["execution_id"],
            "already_processed": durable.get("already_processed", False),
        }

    if body.edited_content:
        result = await queue.edit_and_approve(
            draft_id=draft_id,
            edited_content=body.edited_content,
            reviewed_by=user["sub"],
            company_id=user["company_id"],
        )
        draft["content"] = body.edited_content
    else:
        result = await queue.approve(
            draft_id=draft_id, reviewed_by=user["sub"], company_id=user["company_id"]
        )

    # Another approver may have won the pending -> approved race while
    # this request was evaluating policy. Never repeat the external action.
    if result.get("already_processed"):
        return result

    # Record the execution stage separately from human authorization.
    execution_status = await _execute_approved_draft(draft, adapter)
    result["execution_status"] = execution_status

    # Write audit event
    write_audit_event(
        db,
        company_id=user["company_id"],
        event_type="draft_approved",
        actor_id=user["sub"],
        approved_by=user["sub"],
        action_class=draft.get("action_type"),
        metadata={"draft_id": draft_id, "action_type": draft.get("action_type"),
                  "execution_status": execution_status},
    )

    # Resume linked workflow run if present
    workflow_run_id = draft.get("workflow_run_id")
    if workflow_run_id and execution_status in ("sent", "dispatched"):
        try:
            from app.workflows.engine import WorkflowEngine
            wf_engine = WorkflowEngine(db, user["company_id"])
            wf_engine.resume_after_approval(
                workflow_run_id, approved=True, approved_by=user["sub"]
            )
            log.info("Resumed workflow run %s after draft %s approved", workflow_run_id, draft_id)
        except Exception as exc:
            log.warning("Failed to resume workflow %s after approval: %s", workflow_run_id, exc)

    return result


@router.post("/reject/{draft_id}")
async def reject_draft(
    draft_id: str,
    body: RejectionRequest,
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    """Reject a pending draft action."""
    queue = ApprovalQueue(db)

    # Fetch draft — SCOPED TO THE CALLER'S COMPANY (IDOR fix).
    try:
        draft_result = (
            db.table("drafts").select("*")
            .eq("id", draft_id).eq("company_id", user["company_id"]).execute()
        )
        draft = draft_result.data[0] if draft_result.data else {}
    except Exception:
        draft = {}

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Idempotency: don't re-process an already-resolved draft.
    if draft.get("status") != "pending":
        return {"draft_id": draft_id, "status": draft.get("status"), "already_processed": True}

    result = await queue.reject(
        draft_id=draft_id,
        reviewed_by=user["sub"],
        company_id=user["company_id"],
        reason=body.reason,
    )

    write_audit_event(
        db,
        company_id=user["company_id"],
        event_type="draft_rejected",
        actor_id=user["sub"],
        action_class=draft.get("action_type"),
        metadata={"draft_id": draft_id, "reason": body.reason},
    )

    # Cancel linked workflow run if present
    workflow_run_id = draft.get("workflow_run_id")
    if workflow_run_id:
        try:
            from app.workflows.engine import WorkflowEngine
            wf_engine = WorkflowEngine(db, user["company_id"])
            wf_engine.resume_after_approval(workflow_run_id, approved=False)
            log.info("Cancelled workflow run %s after draft %s rejected", workflow_run_id, draft_id)
        except Exception as exc:
            log.warning("Failed to cancel workflow %s after rejection: %s", workflow_run_id, exc)

    return result


class DraftPORequest(BaseModel):
    item_id: str = Field(min_length=1, max_length=128)
    product_name: str = Field(min_length=1, max_length=255)
    qty: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = Field(default=None, max_length=4000)


@router.post("/draft-po")
async def draft_purchase_order(
    body: DraftPORequest,
    user: dict = Depends(require_permission("draft_purchase_orders")),
    db=Depends(get_db),
):
    """
    Create a Draft PO for a low/critical stock item.
    Queued for human approval before any PO is actually sent to a vendor.
    """
    company_id = user["company_id"]
    queue = ApprovalQueue(db)

    draft_id = await queue.enqueue(
        company_id=company_id,
        action_type="purchase_order",
        content={
            "item_id":      body.item_id,
            "product_name": body.product_name,
            "qty":          body.qty,
            "notes":        body.notes or f"Auto-drafted PO for low stock: {body.product_name}",
        },
        created_by=user["sub"],
    )
    return {
        "status":   "queued",
        "draft_id": draft_id,
        "message":  f"PO draft created for {body.product_name} — pending approval",
    }
