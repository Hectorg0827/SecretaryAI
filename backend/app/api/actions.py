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

If execution definitely did not happen, the draft goes back to "pending" with
the reason, so the queue never shows "approved" for something that failed. If
the outcome is unknown (for example the connection dropped after the request
was sent), the draft is closed as "unknown" and is NOT offered for retry — a
blind retry could send the email or create the order twice.
"""
import logging
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
from app.utils.safe import is_valid_email, log_safe

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


@dataclass
class ExecutionOutcome:
    """What really happened when an approved draft was carried out."""
    status: str          # 'executed' | 'manual_required' | 'failed' | 'unknown'
    detail: str = ""


def _to_decimal(value) -> Optional[Decimal]:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _purchase_order_lines(content: dict) -> Optional[list[dict]]:
    """
    The exact item lines a purchase-order draft would send to QuickBooks, or
    None if the draft is not specific enough to create automatically.

    Every line must name an item, a positive quantity AND a unit cost. A line
    without a cost is refused rather than defaulted to 0: the order's value is
    what the company's spending rules are checked against, so it must be the
    real one.
    """
    raw = content.get("line_items") or []
    if not content.get("vendor_id") or not isinstance(raw, list) or not raw:
        return None
    lines: list[dict] = []
    for li in raw:
        if not isinstance(li, dict):
            return None
        item_id = li.get("item_id") or li.get("qb_item_id")
        quantity = _to_decimal(li.get("quantity"))
        unit_cost = _to_decimal(li.get("unit_cost"))
        if not item_id or quantity is None or quantity <= 0 or unit_cost is None or unit_cost < 0:
            return None
        lines.append({
            "item_id": str(item_id),
            "description": str(li.get("description") or li.get("product_name") or ""),
            "quantity": quantity,
            "unit_cost": unit_cost,
        })
    return lines


def _content_amount(content: dict, action_type: str = "") -> Optional[Decimal]:
    """
    Monetary amount of a draft, for policy checks.

    For a purchase order that will be created from its lines, the amount is
    computed from those exact lines (quantity × unit cost) — never trusted from
    an optional summary field, which could be missing, stale or understated.
    If a summary is also present, the larger of the two is used.
    """
    stated: Optional[Decimal] = None
    for key in ("amount", "total", "total_amount", "subtotal", "value"):
        if content.get(key) is not None:
            stated = _to_decimal(content.get(key))
            break

    if action_type in PURCHASE_ORDER_ACTION_TYPES:
        lines = _purchase_order_lines(content)
        if lines is not None:
            computed = sum((li["quantity"] * li["unit_cost"] for li in lines), Decimal("0"))
            return max(computed, stated) if stated is not None else computed
    return stated


async def _execution_policy_decision(db, company_id: str, requested_by: str,
                                     draft: dict, content: dict):
    """
    Evaluate the tenant PolicyEngine for a COMMIT action about to execute.

    Strict: if the company's rules cannot be loaded this raises
    PolicyUnavailableError and the caller refuses the approval. A rule that
    failed to load must never turn into "allowed".
    """
    action_type = draft.get("action_type", "") or "unknown"
    proposal = ActionProposal(
        action_type=action_type,
        action_class=ActionClass.COMMIT,
        description=f"Execute approved draft {draft.get('id')}",
        company_id=company_id,
        requested_by=requested_by,
        amount=_content_amount(content, action_type),
    )
    return await PolicyEngine(company_id, db).evaluate(proposal, strict=True)


def _definitely_not_done(exc: Exception) -> bool:
    """
    True only when we can be sure the outside system did NOT carry out the
    request: it was never reached, or it answered with a clear rejection.

    Everything else (a timeout after sending, a dropped connection, a 5xx, an
    error we do not recognise) is an UNKNOWN outcome — the email may have gone
    or the order may exist. Those must not be offered for a blind retry, which
    could send the email or create the order a second time.
    """
    import httpx
    from app.connectors.outlook import OutlookAuthError

    if isinstance(exc, OutlookAuthError):
        return True                                   # rejected before sending
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True                                   # never connected
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return 400 <= code < 500 and code != 408      # the request was refused
    if isinstance(exc, (ValueError, RuntimeError)) and "connector configured" in str(exc):
        return True                                   # nothing is connected
    return False


async def _execute_approved_draft(draft: dict, adapter) -> ExecutionOutcome:
    """
    Carry out an approved draft and report honestly what happened.

    Never raises. Outcomes:
      executed        — done;
      manual_required — approved, but a person must do it;
      failed          — definitely NOT done (safe to fix and retry);
      unknown         — it may or may not have happened (do not retry blindly).
    """
    action_type = draft.get("action_type", "") or ""
    content = draft.get("content") or {}

    if action_type in EMAIL_ACTION_TYPES:
        to = str(content.get("to") or content.get("to_email") or "").strip()
        subject = str(content.get("subject") or "").strip()
        body = str(content.get("body") or "").strip()
        if not to or not is_valid_email(to):
            return ExecutionOutcome("failed", "This email has no valid recipient address. Edit the draft and add one.")
        if not body:
            return ExecutionOutcome("failed", "This email has no message text. Edit the draft and add it.")
        try:
            await adapter.send_email_reply(to=to, subject=subject, body=body)
        except Exception as exc:
            log.error("Approved email draft %s did not complete: %s", log_safe(draft.get("id")), log_safe(exc))
            if _definitely_not_done(exc):
                return ExecutionOutcome("failed", "The email was not sent. Check that your mailbox is connected, then try again.")
            return ExecutionOutcome(
                "unknown",
                f"We could not confirm whether the email to {to} was sent. "
                "Check your Sent folder before sending it again.",
            )
        return ExecutionOutcome("executed", f"Email sent to {to}")

    if action_type in PURCHASE_ORDER_ACTION_TYPES:
        lines = _purchase_order_lines(content)
        if lines is None:
            # The draft does not say exactly what to order from whom at what
            # cost, so we do not guess. A person enters it in QuickBooks.
            return ExecutionOutcome(
                "manual_required",
                "Approved. This purchase order does not name a QuickBooks vendor and item lines with "
                "quantities and costs, so it was NOT created automatically — please enter it in QuickBooks.",
            )
        try:
            po = await adapter.create_purchase_order(
                vendor_id=str(content["vendor_id"]),
                line_items=[
                    {**li, "quantity": str(li["quantity"]), "unit_cost": str(li["unit_cost"])}
                    for li in lines
                ],
                memo=str(content.get("notes") or content.get("memo") or "")[:500] or None,
            )
        except Exception as exc:
            log.error("Approved PO draft %s did not complete: %s", log_safe(draft.get("id")), log_safe(exc))
            if _definitely_not_done(exc):
                return ExecutionOutcome("failed", "QuickBooks did not accept the purchase order. Check the vendor and items, then try again.")
            return ExecutionOutcome(
                "unknown",
                "We could not confirm whether QuickBooks created this purchase order. "
                "Look for it in QuickBooks before creating it again.",
            )
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
        log.error("Policy evaluation errored for draft %s — refusing: %s", log_safe(draft_id), log_safe(exc))
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
        log.error("Could not claim draft %s: %s", log_safe(draft_id), log_safe(exc))
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
        event_type={
            "failed": "draft_execution_failed",
            "unknown": "draft_execution_unknown",
        }.get(outcome.status, "draft_executed"),
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
    if workflow_run_id and outcome.status in ("executed", "manual_required"):
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
    product_name: str = ""
    qty: Optional[float] = None
    vendor_id: Optional[str] = None
    unit_cost: Optional[float] = None
    notes: Optional[str] = None


@router.get("/po-options")
async def purchase_order_options(
    item_id: str,
    user: dict = Depends(require_permission("trigger_actions")),
    adapter=Depends(get_adapter),
):
    """
    What the "Draft PO" form needs for one item: its cost and suggested
    quantity, the vendor QuickBooks lists as its usual supplier, and the
    company's vendors to choose from.
    """
    from app.actions.po_builder import po_options
    return await po_options(adapter, item_id)


@router.post("/draft-po")
async def draft_purchase_order(
    body: DraftPORequest,
    user: dict = Depends(require_permission("trigger_actions")),
    db=Depends(get_db),
    adapter=Depends(get_adapter),
):
    """
    Create a purchase-order draft, queued for approval.

    With a vendor, quantity and cost the draft carries the exact QuickBooks
    lines, so approving it creates the order in QuickBooks. Without them it is
    still queued, and approval tells the user to enter it by hand.
    """
    from app.actions.po_builder import POBuildError, build_po_content

    company_id = user["company_id"]
    queue = ApprovalQueue(db)

    try:
        content = await build_po_content(
            adapter,
            item_id=body.item_id,
            product_name=body.product_name,
            vendor_id=body.vendor_id,
            qty=body.qty,
            unit_cost=body.unit_cost,
            notes=body.notes,
        )
    except POBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    draft_id = await queue.enqueue(
        company_id=company_id,
        action_type="purchase_order",
        content=content,
        created_by=user["sub"],
    )
    ready = bool(content.get("line_items"))
    write_audit_event(
        db, company_id=company_id, event_type="draft_created", actor_id=user["sub"],
        action_class="draft", capability="purchase_orders",
        metadata={"draft_id": draft_id, "action_type": "purchase_order",
                  "product_name": content.get("product_name"), "qty": content.get("qty"),
                  "vendor_id": content.get("vendor_id"), "total": content.get("total")},
    )
    name = content.get("product_name") or "this item"
    return {
        "status":   "queued",
        "draft_id": draft_id,
        "ready_for_quickbooks": ready,
        "message": (
            f"PO draft for {name} is waiting for approval. Approving it will create the order in QuickBooks."
            if ready else
            f"PO draft for {name} is waiting for approval. It has no vendor or cost yet, "
            "so it will need to be entered in QuickBooks by hand."
        ),
    }
