"""Only one concurrent approval request may claim and execute a draft."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.actions.approval_queue import ApprovalQueue
from app.api import actions


@pytest.mark.asyncio
@pytest.mark.parametrize("edited", [False, True])
async def test_queue_rejects_losing_conditional_approval(edited):
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    queue = ApprovalQueue(db)
    if edited:
        result = await queue.edit_and_approve(
            "draft1", {"body": "edited"}, "owner1", "co1"
        )
    else:
        result = await queue.approve("draft1", "owner1", "co1")
    assert result["already_processed"] is True
    eq_calls = db.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.call_args_list
    assert eq_calls[-1].args == ("status", "pending")


@pytest.mark.asyncio
async def test_losing_concurrent_approval_never_repeats_external_action():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft1", "status": "pending", "action_type": "email_reply",
         "content": {"to": "a@example.com", "body": "Hi"}}
    ]
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    with patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="allow"), \
         patch.object(actions.ApprovalQueue, "approve", new_callable=AsyncMock,
                      return_value={"draft_id": "draft1", "status": "already_processed",
                                    "already_processed": True}), \
         patch.object(actions, "write_audit_event") as audit:
        response = await actions.approve_draft(
            "draft1", actions.ApprovalRequest(),
            user={"company_id": "co1", "sub": "owner1"}, db=db, adapter=adapter
        )
    assert response["already_processed"] is True
    adapter.send_email_reply.assert_not_awaited()
    audit.assert_not_called()
