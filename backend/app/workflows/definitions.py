"""
Built-in workflow definitions.

How a run moves (see app/workflows/executor.py):
  1. a "draft_*" step creates a real draft in the approval queue;
  2. "await_approval" waits for an owner or manager;
  3. approving the draft performs the "send_*" / "create_*" step — that is the
     only place an email is sent or QuickBooks is written;
  4. any closing step (e.g. logging the outreach) runs, and the run completes.
Rejecting the draft cancels the run.

Each workflow is a list of steps with type, params, autonomy_level, and action_class.

action_class values (mirrors ActionClass enum):
  read        — no side effects, always auto-allowed
  draft       — creates a draft record, always auto-allowed
  commit      — irreversible external action, requires policy evaluation
  destructive — deletes/overwrites data, always requires explicit approval
"""

WORKFLOWS: dict[str, dict] = {
    "low_stock_reorder": {
        "name": "Low Stock Reorder",
        "description": "Automatically draft a PO when stock drops below 2 weeks",
        "trigger_type": "low_stock",
        "steps": [
            {
                "step": 1,
                "name": "Draft Purchase Order",
                "action_type": "draft_purchase_order",
                "action_class": "draft",
                "autonomy": "draft_and_wait",
                "description": "Create draft PO for approval",
            },
            {
                "step": 2,
                "name": "Await Approval",
                "action_type": "await_approval",
                "action_class": "read",
                "autonomy": "draft_and_wait",
                "description": "Wait for manager to approve the PO",
                "timeout_hours": 48,
            },
            {
                "step": 3,
                "name": "Create Purchase Order",
                "action_type": "create_purchase_order",
                "action_class": "commit",
                "autonomy": "draft_and_wait",
                "description": "Approval creates the order in QuickBooks (or tells you to enter it by hand)",
            },
        ],
    },
    "overdue_outreach": {
        "name": "Overdue Account Outreach",
        "description": "Draft a payment reminder when invoice is 15+ days overdue",
        "trigger_type": "overdue_invoice",
        "steps": [
            {
                "step": 1,
                "name": "Draft Payment Reminder",
                "action_type": "draft_customer_email",
                "action_class": "draft",
                "autonomy": "draft_and_wait",
                "description": "Draft a professional payment reminder email",
            },
            {
                "step": 2,
                "name": "Await Send Approval",
                "action_type": "await_approval",
                "action_class": "read",
                "autonomy": "draft_and_wait",
                "description": "Manager reviews and approves sending",
                "timeout_hours": 24,
            },
            {
                "step": 3,
                "name": "Send Reminder Email",
                "action_type": "send_customer_email",
                "action_class": "commit",
                "autonomy": "draft_and_wait",
                "description": "Send the approved payment reminder",
            },
            {
                "step": 4,
                "name": "Log Outreach",
                "action_type": "generate_internal_report",
                "action_class": "read",
                "autonomy": "autonomous",
                "description": "Record outreach attempt in account notes",
            },
        ],
    },
    "dormant_account_reactivation": {
        "name": "Dormant Account Reactivation",
        "description": "Reach out to accounts that haven't ordered in 45+ days",
        "trigger_type": "dormant_account",
        "steps": [
            {
                "step": 1,
                "name": "Draft Reactivation Email",
                "action_type": "draft_customer_email",
                "action_class": "draft",
                "autonomy": "draft_and_wait",
                "description": "Draft a personalized check-in email",
            },
            {
                "step": 2,
                "name": "Await Send Approval",
                "action_type": "await_approval",
                "action_class": "read",
                "autonomy": "draft_and_wait",
                "description": "Sales rep reviews the draft",
                "timeout_hours": 48,
            },
            {
                "step": 3,
                "name": "Send Reactivation Email",
                "action_type": "send_customer_email",
                "action_class": "commit",
                "autonomy": "draft_and_wait",
                "description": "Send the approved reactivation email",
            },
        ],
    },
}
