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
    user: dict = Depends(get_current_user),
    db=Depends(get_db),                     # ← was `db = None` (bug)
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

    # Fetch the draft before approving so we can execute it
    try:
        draft_result = db.table("drafts").select("*").eq("id", draft_id).execute()
        draft = draft_result.data[0] if draft_result.data else {}
    except Exception:
        draft = {}

    if body.edited_content:
        result = await queue.edit_and_approve(
            draft_id=draft_id,
            edited_content=body.edited_content,
            reviewed_by=user["sub"],
        )
        draft["content"] = body.edited_content
    else:
        result = await queue.approve(draft_id=draft_id, reviewed_by=user["sub"])

    # Execute the approved action
    if draft:
        await _execute_approved_draft(draft, adapter)

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
    return await queue.reject(
        draft_id=draft_id,
        reviewed_by=user["sub"],
        reason=body.reason,
    )


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
