"""
Action approval endpoints — manage the DRAFT_AND_WAIT queue.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user, require_permission
from app.actions.approval_queue import ApprovalQueue

router = APIRouter()


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
):
    """Approve a pending draft action. Optionally include edited content."""
    queue = ApprovalQueue(db)

    if body.edited_content:
        result = await queue.edit_and_approve(
            draft_id=draft_id,
            edited_content=body.edited_content,
            reviewed_by=user["sub"],
        )
    else:
        result = await queue.approve(draft_id=draft_id, reviewed_by=user["sub"])

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
