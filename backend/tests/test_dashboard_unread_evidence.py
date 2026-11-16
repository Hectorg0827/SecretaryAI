"""Backend contract tests for bounded dashboard unread-email evidence.

Synthetic fixtures only: no OAuth tokens, provider calls, live mailboxes or DB writes.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.dashboard import get_dashboard_summary


def _snapshot():
    return {
        "payload": {
            "accounts": {
                "healthy": 1, "slowing": 0, "at_risk": 0, "dormant": 0,
                "total": 1,
            },
            "inventory_alerts": [],
            "sales_30d": 100,
            "scoring_policy_version": 2,
            "source_quality": "partial",
            "source_read_status": "completed",
        },
        "generated_at": "2026-11-15T12:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_count,expected_capped",
    [(0, False), (1, False), (49, False), (50, True)],
)
async def test_dashboard_marks_full_unread_page_as_lower_bound(message_count, expected_capped):
    adapter = AsyncMock()
    adapter.get_emails.return_value = [
        {"id": f"synthetic-{i}"} for i in range(message_count)
    ]
    with patch("app.api.dashboard.read_snapshot", return_value=_snapshot()):
        result = await get_dashboard_summary(
            user={"company_id": "synthetic-company", "sub": "owner", "role": "owner"},
            adapter=adapter,
            db=MagicMock(),
        )
    assert result["unread_emails"] == message_count
    assert result["unread_emails_capped"] is expected_capped
    adapter.get_emails.assert_awaited_once_with(
        query="is:unread", max_results=50,
    )


@pytest.mark.asyncio
async def test_dashboard_preserves_unavailable_mailbox_as_unknown():
    adapter = AsyncMock()
    adapter.get_emails.side_effect = ConnectionError("synthetic provider outage")
    with patch("app.api.dashboard.read_snapshot", return_value=_snapshot()):
        result = await get_dashboard_summary(
            user={"company_id": "synthetic-company", "sub": "owner", "role": "owner"},
            adapter=adapter,
            db=MagicMock(),
        )
    assert result["unread_emails"] is None
    assert result["unread_emails_capped"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "sales_rep", "back_office", "unknown"])
async def test_dashboard_does_not_read_or_disclose_mailbox_for_unauthorized_roles(role):
    adapter = AsyncMock()
    with patch("app.api.dashboard.read_snapshot", return_value=_snapshot()):
        result = await get_dashboard_summary(
            user={"company_id": "synthetic-company", "sub": "employee", "role": role},
            adapter=adapter,
            db=MagicMock(),
        )
    assert result["unread_emails"] is None
    assert result["unread_emails_capped"] is None
    adapter.get_emails.assert_not_awaited()
