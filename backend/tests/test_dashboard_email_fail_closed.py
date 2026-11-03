"""Disconnected or broken email connectors must fail closed, not respond as empty."""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.dashboard import list_emails


@pytest.mark.asyncio
async def test_disconnected_email_connector_returns_safe_503():
    adapter = AsyncMock()
    adapter.get_emails.side_effect = RuntimeError("provider secret should not leak")

    with pytest.raises(HTTPException) as caught:
        await list_emails(
            unread_only=False, limit=20, user={"company_id": "c1"}, adapter=adapter,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == "Email source unavailable"
    assert "secret" not in caught.value.detail.lower()
