"""Approved business actions must not be reported as executed on failure."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import actions


@pytest.mark.asyncio
async def test_email_send_failure_is_not_reported_as_sent():
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(side_effect=OSError("mail service unavailable"))
    status = await actions._execute_approved_draft(
        {"id": "d1", "action_type": "email_reply",
         "content": {"to": "a@example.com", "body": "Hello"}}, adapter
    )
    assert status == "failed"
    adapter.send_email_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_email_send_success_reports_sent():
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    status = await actions._execute_approved_draft(
        {"id": "d1", "action_type": "email_reply",
         "content": {"to": "a@example.com", "subject": "Hi", "body": "Hello"}},
        adapter,
    )
    assert status == "sent"


@pytest.mark.asyncio
async def test_unsupported_action_never_claims_execution():
    assert await actions._execute_approved_draft(
        {"id": "d1", "action_type": "unknown", "content": {}}, MagicMock()
    ) == "unsupported"


@pytest.mark.asyncio
async def test_approval_reports_execution_failure_without_resuming_workflow():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "d1", "status": "pending", "action_type": "email_reply",
         "workflow_run_id": "workflow-1",
         "content": {"to": "a@example.com", "body": "Hello"}}
    ]
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(side_effect=OSError("offline"))
    with patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="allow"), \
         patch.object(actions, "write_audit_event") as audit, \
         patch("app.workflows.engine.WorkflowEngine") as workflow:
        result = await actions.approve_draft(
            "d1", actions.ApprovalRequest(),
            user={"company_id": "co1", "sub": "owner1"},
            db=db, adapter=adapter,
        )
    assert result["status"] == "approved"
    assert result["execution_status"] == "failed"
    workflow.return_value.resume_after_approval.assert_not_called()
    assert audit.call_args.kwargs["metadata"]["execution_status"] == "failed"
