"""
Action approval endpoints — manage the DRAFT_AND_WAIT queue.

Nothing with an outside effect (an email, a purchase order) happens until a
person with the approver role approves the exact draft. Approval then runs
these steps, in this order, and stops at the first one that fails:

  1. the draft is loaded for the caller's company only;
  2. the company's action policy is checked (a failure to check = refuse);
  3. the draft is claimed atomically, so two clicks can never send twice;
  4. an audit record is written — if it cannot be, nothing is executed;
  5. the action is executed and its real outcome is stored on the draft;
  6. the outcome is audited.

If execution fails, the draft goes back to "pending" with the reason, so the
queue never shows "approved" for something that did not happen.
"""
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db, get_adapter
from app.auth.rbac import get_current_user, require_permission
from app.actions.approval_queue import ApprovalQueue
from app.domain.contracts import ActionClass, ActionProposal
from app.domain.policy import PolicyEngine, PolicyUnavailableError
from app.utils.audit import AuditWriteError, write_audit_event

log = logging.getLogger(__name__)
router = APIRouter()

# Draft action types, grouped by what approving them does. The AI layer, the
# workflow definitions and the UI historically used different names for the
# same thing; all of them are recognised here so an approved draft is never
# silently ignored.
EMAIL_ACTION_TYPES = frozenset({
    "email_reply", "customer_email", "draft_customer_email",
    "send_customer_email", "send_vendor_email",
})
PURCHASE_ORDER_ACTION_TYPES = frozenset({"purchase_order", "draft_purchase_order"})

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ExecutionOutcome:
    """What really happened when an approved draft was carried out."""
    status: str          # 'executed' | 'manual_required' | 'failed'
    detail: str = ""


def _content_amount(content: dict) -> Optional[Decimal]:
    """Best-effort monetary amount from a draft's content for policy checks."""
    for key in ("amount", "total", "total_amount", "subtotal", "value"):
        v = content.get(key)
        if v is not None:
            try:
                return Decimal(str(v))
            except (InvalidOperation, ValueError, TypeError):
                return None
    return None


async def _execution_policy_decision(db, company_id: str, requested_by: str,
                                     draft: dict, content: dict):
    """
    Evaluate the tenant PolicyEngine for a COMMIT action about to execute.

    Strict: if the company's rules cannot be loaded this raises
    PolicyUnavailableError and the caller refuses the approval. A rule that
    failed to load must never turn into "allowed".
    """
    proposal = ActionProposal(
        action_type=draft.get("action_type", "") or "unknown",
        action_class=ActionClass.COMMIT,
        description=f"Execute approved draft {draft.get('id')}",
        company_id=company_id,
        requested_by=requested_by,
        amount=_content_amount(content),
    )
    return await PolicyEngine(company_id, db).evaluate(proposal, strict=True)


async def _execute_approved_draft(draft: dict, adapter) -> ExecutionOutcome:
    """
    Carry out an approved draft and report honestly what happened.

    Never raises: every failure becomes ExecutionOutcome('failed', reason) so
    the caller can put the draft back in the queue.
    """
    action_type = draft.get("action_type", "") or ""
    content = draft.get("content") or {}

    if action_type in EMAIL_ACTION_TYPES:
        to = str(content.get("to") or content.get("to_email") or "").strip()
        subject = str(content.get("subject") or "").strip()
        body = str(content.get("body") or "").strip()
        if not to or not _EMAIL_RE.match(to):
            return ExecutionOutcome("failed", "This email has no valid recipient address. Edit the draft and add one.")
        if not body:
            return ExecutionOutcome("failed", "This email has no message text. Edit the draft and add it.")
        try:
            await adapter.send_email_reply(to=to, subject=subject, body=body)
        except Exception as exc:
            log.error("Approved email draft %s failed to send: %s", draft.get("id"), exc)
            return ExecutionOutcome("failed", f"The email could not be sent: {exc}")
        return ExecutionOutcome("executed", f"Email sent to {to}")

    if action_type in PURCHASE_ORDER_ACTION_TYPES:
        vendor_id = content.get("vendor_id")
        line_items = content.get("line_items") or []
        usable_lines = [
            li for li in line_items
            if isinstance(li, dict) and (li.get("item_id") or li.get("qb_item_id")) and li.get("quantity")
        ]
        if not vendor_id or not usable_lines or len(usable_lines) != len(line_items):
            # The draft does not say exactly what to order from whom, so we do
            # not guess. A person enters it in QuickBooks.
            return ExecutionOutcome(
                "manual_required",
                "Approved. This purchase order does not name a QuickBooks vendor and item lines, "
                "so it was NOT created automatically — please enter it in QuickBooks.",
            )
        try:
            po = await adapter.create_purchase_order(
                vendor_id=str(vendor_id),
                line_items=[
                    {
                        "item_id": str(li.get("item_id") or li.get("qb_item_id")),
                        "description": str(li.get("description") or li.get("product_name") or ""),
                        "quantity": li["quantity"],
                        "unit_cost": li.get("unit_cost", 0),
                    }
                    for li in usable_lines
                ],
                memo=str(content.get("notes") or content.get("memo") or "")[:500] or None,
            )
        except Exception as exc:
            log.error("Approved PO draft %s failed in QuickBooks: %s", draft.get("id"), exc)
            return ExecutionOutcome("failed", f"QuickBooks did not accept the purchase order: {exc}")
        return ExecutionOutcome("executed", f"Purchase order created in QuickBooks (id {getattr(po, 'qb_id', '?')})")

    return ExecutionOutcome(
        "manual_required",
        "Approved. There is no automatic step for this kind of action — please carry it out yourself.",
    )


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
    company_id = user["company_id"]

    # 1. Fetch the draft — SCOPED TO THE CALLER'S COMPANY. A draft belonging to
    # another tenant is invisible here, so cross-company approval is impossible.
    try:
        draft_result = (
            db.table("drafts").select("*")
            .eq("id", draft_id).eq("company_id", company_id).execute()
        )
        draft = draft_result.data[0] if draft_result.data else {}
    except Exception:
        draft = {}

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Only a still-pending draft may be approved/executed.
    if draft.get("status") != "pending":
        return {"draft_id": draft_id, "status": draft.get("status"), "already_processed": True}

    edited = body.edited_content is not None and bool(body.edited_content)
    content = body.edited_content if edited else (draft.get("content") or {})

    # 2. Policy gate — before anything changes.
    try:
        decision = await _execution_policy_decision(db, company_id, user["sub"], draft, content)
    except PolicyUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="Your company's action policy could not be checked, so nothing was approved. Please try again.",
        )
    except Exception as exc:
        log.error("Policy evaluation errored for draft %s — refusing: %s", draft_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Your company's action policy could not be checked, so nothing was approved. Please try again.",
        )

    if decision.effect == "deny":
        write_audit_event(
            db, company_id=company_id, event_type="policy_blocked", actor_id=user["sub"],
            action_class="commit", policy_rule_id=decision.rule_id,
            metadata={"draft_id": draft_id, "action_type": draft.get("action_type"), "rule": decision.rule_name},
        )
        raise HTTPException(status_code=403, detail="This action is blocked by your company's action policy")
    if decision.effect not in ("allow", "require_approval"):
        # e.g. 'require_mfa' — an effect this endpoint cannot satisfy.
        raise HTTPException(status_code=403, detail="This action needs a stronger approval than is available here")
    if decision.effect == "require_approval":
        allowed_roles = decision.approval_roles or ["owner", "manager"]
        if user.get("role") not in allowed_roles:
            raise HTTPException(status_code=403, detail="Your role cannot approve this action")

    # 3. Atomic claim: exactly one approval wins.
    try:
        claimed = await queue.claim(draft_id, company_id, user["sub"], body.edited_content if edited else None)
    except Exception as exc:
        log.error("Could not claim draft %s: %s", draft_id, exc)
        raise HTTPException(status_code=503, detail="Could not approve right now. Nothing was sent. Please try again.")
    if not claimed:
        return {"draft_id": draft_id, "status": "processing", "already_processed": True}

    draft["content"] = content

    # 4. Audit BEFORE acting. No record → no action.
    try:
        write_audit_event(
            db,
            company_id=company_id,
            event_type="draft_approved",
            actor_id=user["sub"],
            approved_by=user["sub"],
            action_class="commit",
            policy_rule_id=decision.rule_id,
            metadata={
                "draft_id": draft_id,
                "action_type": draft.get("action_type"),
                "edited": edited,
                "created_by": draft.get("created_by"),
                "policy_effect": decision.effect,
            },
            required=True,
        )
    except AuditWriteError:
        await queue.release(draft_id, company_id, "Not executed: the audit log was unavailable.")
        raise HTTPException(
            status_code=503,
            detail="The activity log is unavailable, so nothing was sent. Please try again.",
        )

    # 5. Execute, and store what really happened.
    outcome = await _execute_approved_draft(draft, adapter)

    if outcome.status == "failed":
        await queue.release(draft_id, company_id, outcome.detail)
        final_status = "pending"
    else:
        final_status = await queue.finish(draft_id, company_id, edited, outcome.status, outcome.detail)

    # 6. Audit the outcome.
    write_audit_event(
        db,
        company_id=company_id,
        event_type="draft_executed" if outcome.status != "failed" else "draft_execution_failed",
        actor_id=user["sub"],
        action_class="commit",
        metadata={
            "draft_id": draft_id,
            "action_type": draft.get("action_type"),
            "execution_status": outcome.status,
            "detail": outcome.detail[:500],
        },
    )

    # Resume the linked workflow only if the step really went through.
    workflow_run_id = draft.get("workflow_run_id")
    if workflow_run_id and outcome.status != "failed":
        try:
            from app.workflows.engine import WorkflowEngine
            WorkflowEngine(db, company_id).resume_after_approval(
                workflow_run_id, approved=True, approved_by=user["sub"]
            )
        except Exception as exc:
            log.warning("Failed to resume workflow %s after approval: %s", workflow_run_id, exc)

    return {
        "draft_id": draft_id,
        "status": final_status,
        "execution_status": outcome.status,
        "execution_detail": outcome.detail,
    }


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
    if result.get("status") != "rejected":
        return {"draft_id": draft_id, "status": "processing", "already_processed": True}

    write_audit_event(
        db,
        company_id=user["company_id"],
        event_type="draft_rejected",
        actor_id=user["sub"],
        action_class="draft",
        metadata={"draft_id": draft_id, "action_type": draft.get("action_type"), "reason": body.reason},
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
    user: dict = Depends(require_permission("trigger_actions")),
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
    write_audit_event(
        db, company_id=company_id, event_type="draft_created", actor_id=user["sub"],
        action_class="draft", capability="purchase_orders",
        metadata={"draft_id": draft_id, "action_type": "purchase_order", "product_name": body.product_name, "qty": body.qty},
    )
    return {
        "status":   "queued",
        "draft_id": draft_id,
        "message":  f"PO draft created for {body.product_name} — pending approval",
    }
