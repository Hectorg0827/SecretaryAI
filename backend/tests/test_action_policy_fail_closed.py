"""An unavailable company policy must never authorize an AI-initiated action."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import actions


@pytest.mark.asyncio
async def test_policy_engine_outage_denies_commit():
    db = MagicMock()
    draft = {"id": "draft-1", "action_type": "purchase_order"}
    with patch.object(actions.PolicyEngine, "evaluate", new_callable=AsyncMock,
                      side_effect=ConnectionError("policy unavailable")):
        effect = await actions._execution_policy_effect(
            db, "company-1", "owner-1", draft, {"amount": "5000"}
        )
    assert effect == "deny"


@pytest.mark.asyncio
async def test_policy_outage_never_approves_or_executes_draft():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft-1", "status": "pending", "action_type": "email_reply",
         "content": {"to": "customer@example.com", "body": "Draft only"}}
    ]
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    with patch.object(actions.PolicyEngine, "evaluate", new_callable=AsyncMock,
                      side_effect=ConnectionError("policy unavailable")), \
         patch.object(actions.ApprovalQueue, "approve", new_callable=AsyncMock) as approve:
        with pytest.raises(HTTPException) as error:
            await actions.approve_draft(
                "draft-1", actions.ApprovalRequest(),
                user={"company_id": "company-1", "sub": "owner-1"},
                db=db, adapter=adapter,
            )
    assert error.value.status_code == 403
    approve.assert_not_awaited()
    adapter.send_email_reply.assert_not_awaited()
