"""
Workflow step executor — makes each workflow step actually do its work.

Until now a workflow run only moved a counter: "Draft Purchase Order" created
no draft, "Send Reminder Email" sent nothing. This module performs the steps:

  draft_purchase_order   → builds a real PO draft (vendor, quantity, cost from
                           QuickBooks) and puts it in the approval queue
  draft_customer_email   → writes a real email draft to the customer and puts
                           it in the approval queue
  await_approval         → stops; the run waits for a person
  send_* / create_*      → carried out BY the approval itself (the approve
                           endpoint sends the email / creates the order); this
                           step just records that outcome
  generate_internal_report → records the completed outreach in the audit trail

Nothing with an outside effect happens here. Emails and QuickBooks writes only
ever happen in the approve endpoint, after its policy check and audit record.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.actions.approval_queue import ApprovalQueue
from app.utils.audit import write_audit_event
from app.utils.safe import is_valid_email, log_safe
from app.workflows.definitions import WORKFLOWS
from app.workflows.engine import WorkflowEngine

log = logging.getLogger(__name__)

# Steps whose work is done by approving the draft created earlier in the run.
DONE_BY_APPROVAL = {"send_customer_email", "send_vendor_email", "create_purchase_order"}
_MAX_STEPS_PER_PASS = 10


def _customer_for(customers: list, trigger: dict):
    """Find the QuickBooks customer a trigger refers to (by ID, else exact name)."""
    cid = str(trigger.get("customer_id") or "")
    name = str(trigger.get("account_name") or "").strip().lower()
    for c in customers or []:
        if cid and str(getattr(c, "qb_id", "")) == cid:
            return c
    if name:
        matches = [c for c in customers or [] if str(getattr(c, "name", "")).strip().lower() == name]
        if len(matches) == 1:      # never guess between two customers with one name
            return matches[0]
    return None


def build_email_draft(workflow_name: str, trigger: dict, customer, company_name: str) -> dict:
    """
    A plain, editable email draft for the approver. Deliberately a fixed
    template (no model call): the wording is predictable, and nothing from
    outside the company can steer what the draft says.
    """
    account = getattr(customer, "name", None) or trigger.get("account_name") or "there"
    email = getattr(customer, "email", None) or ""
    to = email.strip() if is_valid_email((email or "").strip()) else ""
    sender = company_name or "our team"

    if workflow_name == "overdue_outreach":
        balance = trigger.get("balance")
        amount = f" of ${float(balance):,.2f}" if isinstance(balance, (int, float)) else ""
        subject = "A quick reminder about your open invoice"
        body = (
            f"Hello {account},\n\n"
            f"Our records show an open balance{amount} that is now past its due date. "
            "If payment is already on its way, thank you — please disregard this note.\n\n"
            "If you have any questions about the invoice, just reply to this email and we will help.\n\n"
            f"Thank you,\n{sender}"
        )
    else:  # dormant_account_reactivation and any other customer check-in
        subject = f"Checking in from {sender}"
        body = (
            f"Hello {account},\n\n"
            "It has been a little while since your last order, so we wanted to check in. "
            "Is there anything you are running low on, or anything we can do better for you?\n\n"
            "Reply to this email any time and we will take care of it.\n\n"
            f"Best regards,\n{sender}"
        )
    return {
        "to": to,
        "subject": subject,
        "body": body,
        "account_name": account,
        "customer_id": str(getattr(customer, "qb_id", "") or trigger.get("customer_id") or ""),
        **({} if to else {"needs": "This customer has no email address in QuickBooks. Add one before approving."}),
    }


async def run_steps(db, adapter, company: dict, run_id: str,
                    approval_detail: Optional[str] = None) -> str:
    """
    Carry a run forward until it has to wait for a person, finishes or fails.
    Returns the run's status afterwards. Safe to call again on the same run.
    """
    company_id = company["id"]
    engine = WorkflowEngine(db, company_id)
    queue = ApprovalQueue(db)

    for _ in range(_MAX_STEPS_PER_PASS):
        run = engine._get_run(run_id)
        if not run:
            return "missing"
        if run.get("status") != "running":
            return run.get("status")
        step = engine.get_current_step_def(run_id)
        if step is None:
            engine.advance(run_id, {"note": "no further steps"})
            continue

        action = step.get("action_type")
        trigger = run.get("trigger_data") or {}
        workflow_name = run.get("workflow_name", "")

        try:
            if action == "draft_purchase_order":
                from app.actions.po_builder import build_po_content
                content = await build_po_content(
                    adapter,
                    item_id=str(trigger.get("item_id") or ""),
                    product_name=str(trigger.get("product_name") or ""),
                    use_preferred_vendor=True,
                    notes=f"Low stock: {trigger.get('product_name') or 'item'}",
                )
                draft_id = await queue.enqueue(
                    company_id=company_id, action_type="purchase_order", content=content,
                    created_by="ai", workflow_run_id=run_id,
                )
                write_audit_event(
                    db, company_id=company_id, event_type="draft_created", actor_id="ai",
                    action_class="draft", capability="purchase_orders",
                    metadata={"draft_id": draft_id, "workflow_run_id": run_id, "workflow": workflow_name,
                              "ready_for_quickbooks": bool(content.get("line_items"))},
                )
                engine.advance(run_id, {"draft_id": draft_id}, awaiting_entity_id=draft_id)

            elif action == "draft_customer_email":
                customers = await adapter.get_all_customers()
                customer = _customer_for(customers, trigger)
                content = build_email_draft(workflow_name, trigger, customer, company.get("name") or "")
                draft_id = await queue.enqueue(
                    company_id=company_id, action_type="draft_customer_email", content=content,
                    created_by="ai", workflow_run_id=run_id,
                )
                write_audit_event(
                    db, company_id=company_id, event_type="draft_created", actor_id="ai",
                    action_class="draft", capability="email",
                    metadata={"draft_id": draft_id, "workflow_run_id": run_id, "workflow": workflow_name,
                              "has_recipient": bool(content.get("to"))},
                )
                engine.advance(run_id, {"draft_id": draft_id}, awaiting_entity_id=draft_id)

            elif action == "await_approval":
                # Reached only if a run is 'running' on an await step with
                # nothing to wait for — park it so a person can see it.
                db.table("workflow_runs").update({"status": "awaiting_approval"}).eq(
                    "id", run_id).eq("company_id", company_id).execute()

            elif action in DONE_BY_APPROVAL:
                engine.advance(run_id, {"done_by_approval": True, "detail": (approval_detail or "")[:300]})

            elif action == "generate_internal_report":
                write_audit_event(
                    db, company_id=company_id, event_type="workflow_outreach_logged", actor_id="ai",
                    capability="workflows",
                    metadata={"workflow_run_id": run_id, "workflow": workflow_name,
                              "account_name": trigger.get("account_name")},
                )
                engine.advance(run_id, {"logged": True})

            else:
                engine.fail(run_id, f"Unknown step type: {action}")
        except Exception as exc:
            log.error("Workflow %s step '%s' failed: %s", log_safe(run_id), log_safe(action), log_safe(exc))
            engine.fail(run_id, "A step could not be completed.")

    run = engine._get_run(run_id)
    return (run or {}).get("status", "missing")


async def start_from_event(db, adapter, company: dict, event: dict) -> Optional[str]:
    """Start the workflow for a feed event and run it up to its approval step."""
    workflow_name = {
        "low_stock": "low_stock_reorder",
        "dormant_account": "dormant_account_reactivation",
        "overdue_invoice": "overdue_outreach",
    }.get(event.get("event_type"))
    if not workflow_name or workflow_name not in WORKFLOWS:
        return None
    engine = WorkflowEngine(db, company["id"])
    trigger = dict(event.get("action_data") or {})
    trigger["feed_event_id"] = str(event.get("id") or "")
    trigger.setdefault("entity_id", event.get("entity_id"))
    run_id = engine.start(workflow_name, trigger_data=trigger)
    await run_steps(db, adapter, company, run_id)
    return run_id


async def after_approval(db, adapter, company_id: str, run_id: str, approved_by: str,
                         execution_status: str, detail: str) -> None:
    """Called by the approve endpoint once the linked draft has been handled."""
    engine = WorkflowEngine(db, company_id)
    if execution_status == "failed":
        return                      # the draft is back in the queue; keep waiting
    if execution_status == "unknown":
        return                      # awaiting human reconciliation, not a failed run
    engine.resume_after_approval(run_id, approved=True, approved_by=approved_by)
    company = {"id": company_id, "name": ""}
    try:
        rows = db.table("companies").select("id, name").eq("id", company_id).execute().data
        if isinstance(rows, list) and rows:
            company = rows[0]
    except Exception:
        pass
    await run_steps(db, adapter, company, run_id, approval_detail=detail)
