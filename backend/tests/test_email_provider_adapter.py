"""No live provider sends; test only mocked Gmail/Outlook receipts."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows.email_provider_adapter import (
    AmbiguousEmailAcknowledgement,
    LiveEmailDeliveryDisabled,
    send_approved_email_via_provider,
)


@pytest.mark.asyncio
async def test_live_provider_disabled_without_two_explicit_flags(monkeypatch):
    monkeypatch.delenv("SECRETARYAI_EMAIL_DELIVERY_MODE", raising=False)
    monkeypatch.delenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", raising=False)
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    with pytest.raises(LiveEmailDeliveryDisabled):
        await send_approved_email_via_provider(
            adapter, to="synthetic@example.test", subject="Hi", body="Approved"
        )
    adapter.send_email_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_provider_receipt_is_only_provider_acceptance(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_EMAIL_DELIVERY_MODE", "live")
    monkeypatch.setenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", "1")
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(return_value={
        "status": "sent", "message_id": "synthetic-gmail-1",
    })
    receipt = await send_approved_email_via_provider(
        adapter, to="synthetic@example.test", subject="Hi", body="Approved"
    )
    assert receipt == {
        "status": "accepted_by_provider",
        "provider_message_id": "synthetic-gmail-1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    None, {}, {"status": "failed"}, {"status": "queued"}, "sent",
])
async def test_unknown_provider_response_needs_reconciliation(monkeypatch, reply):
    monkeypatch.setenv("SECRETARYAI_EMAIL_DELIVERY_MODE", "live")
    monkeypatch.setenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", "1")
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(return_value=reply)
    with pytest.raises(AmbiguousEmailAcknowledgement):
        await send_approved_email_via_provider(
            adapter, to="synthetic@example.test", subject="Hi", body="Approved"
        )
    adapter.send_email_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_recipient_never_calls_provider(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_EMAIL_DELIVERY_MODE", "live")
    monkeypatch.setenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", "1")
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    with pytest.raises(ValueError):
        await send_approved_email_via_provider(
            adapter, to="", subject="", body="Approved"
        )
    adapter.send_email_reply.assert_not_awaited()
