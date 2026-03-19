"""
Action approval endpoints — manage the DRAFT_AND_WAIT queue.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.auth.rbac import get_current_user, require_permission
from app.actions.approval_queue import ApprovalQueue

router = APIRouter()


@router.get("/pending")
async def list_pending_actions(user: dict = Depends(get_current_user)):
    """List all drafts awaiting approval for this company."""
    company_id = user["company_id"]
    # TODO: inject real DB session
    db = None
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
):
    """Approve a pending draft action. Optionally include edited content."""
    db = None
    queue = ApprovalQueue(db)

    if body.edited_content:
        result = await queue.edit_and_approve(
            draft_id=draft_id,
            edited_content=body.edited_content,
            reviewed_by=user["sub"],
        )
    else:
        result = await queue.approve(draft_id=draft_id, reviewed_by=user["sub"])

    # TODO: Execute the approved action (call action engine with the draft content)
    return result


@router.post("/reject/{draft_id}")
async def reject_draft(
    draft_id: str,
    body: RejectionRequest,
    user: dict = Depends(require_permission("approve_actions")),
):
    """Reject a pending draft action."""
    db = None
    queue = ApprovalQueue(db)
    return await queue.reject(
        draft_id=draft_id,
        reviewed_by=user["sub"],
        reason=body.reason,
    )
