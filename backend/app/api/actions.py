"""
Action approval endpoints — manage the DRAFT_AND_WAIT queue.
After approval, actions are executed immediately for email_reply type
and dispatched as Celery tasks for heavier operations.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db, get_adapter
from app.auth.rbac import get_current_user, require_permission
from app.actions.approval_queue import ApprovalQueue
from app.utils.audit import write_audit_event

log = logging.getLogger(__name__)
router = APIRouter()


async def _execute_approved_draft(draft: dict, adapter) -> None:
    """Execute a draft action after it has been approved."""
    action_type = draft.get("action_type", "")
    content = draft.get("content", {})

    if action_type == "email_reply":
        to = content.get("to", "")
        subject = content.get("subject", "")
        body = content.get("body", "")
        if to and body:
            try:
                await adapter.send_email_reply(to=to, subject=subject, body=body)
                log.info("Executed approved email_reply draft %s to %s", draft.get("id"), to)
            except Exception as exc:
                log.error("Failed to execute email_reply draft %s: %s", draft.get("id"), exc)

    elif action_type == "purchase_order":
        # Dispatch to Celery for async processing (vendor integration required)
        try:
            from celery_app import celery_app
            celery_app.send_task(
                "tasks.actions.process_approved_po",
                kwargs={"draft_id": draft.get("id"), "content": content},
            )
            log.info("Dispatched approved PO draft %s to Celery", draft.get("id"))
        except Exception as exc:
            log.warning("Could not dispatch PO task (non-fatal): %s", exc)


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

    # Execute the approved action
    if draft:
        await _execute_approved_draft(draft, adapter)

    # Write audit event
    write_audit_event(
        db,
        company_id=user["company_id"],
        event_type="draft_approved",
        actor_id=user["sub"],
        approved_by=user["sub"],
        action_class=draft.get("action_type"),
        metadata={"draft_id": draft_id, "action_type": draft.get("action_type")},
    )

    # Resume linked workflow run if present
    workflow_run_id = draft.get("workflow_run_id")
    if workflow_run_id:
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
    item_id: str
    product_name: str
    qty: Optional[int] = None
    notes: Optional[str] = None


@router.post("/draft-po")
async def draft_purchase_order(
    body: DraftPORequest,
    user: dict = Depends(get_current_user),
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
