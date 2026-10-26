"""Prevent unapproved scheduled sends from bypassing the staging email pilot."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _setup_mocked_generation():
    response = MagicMock()
    response.content = [MagicMock(text="A private generated briefing")]
    ai = AsyncMock()
    ai.messages.create.return_value = response
    return ai


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,approved", [("live", "1"), ("disabled", "1"), ("live", "0")])
async def test_staging_must_never_send_and_has_no_sent_timestamp(monkeypatch, mode, approved):
    import app.scheduler.morning_briefing as module

    monkeypatch.setenv("SECRETARYAI_DEPLOYMENT_ENV", "staging")
    monkeypatch.setenv("SECRETARYAI_EMAIL_DELIVERY_MODE", mode)
    monkeypatch.setenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", approved)
    monkeypatch.setattr(module.settings, "sendgrid_api_key", "fake-test-key")
    db = MagicMock()
    with (
        patch.object(module.anthropic, "AsyncAnthropic", return_value=_setup_mocked_generation()),
        patch.object(module, "SendGridAPIClient") as sender,
        patch("app.api.deps.get_db", return_value=db),
    ):
        result = await module.generate_morning_briefing(
            company_id="co-1", company_name="Example",
            preferred_language="English", data_summary="fake input",
            recipient_email="staging@example.test",
        )
    assert result == "A private generated briefing"
    sender.assert_not_called()
    row = db.table.return_value.insert.call_args.args[0]
    assert row["sent_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status,should_mark_sent", [(202, True), (200, False), (500, False)])
async def test_production_requires_provider_acceptance(monkeypatch, http_status, should_mark_sent):
    import app.scheduler.morning_briefing as module

    monkeypatch.setenv("SECRETARYAI_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("SECRETARYAI_EMAIL_DELIVERY_MODE", "live")
    monkeypatch.setenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", "1")
    monkeypatch.setattr(module.settings, "sendgrid_api_key", "fake-test-key")
    db = MagicMock()
    sender = MagicMock()
    sender.send.return_value.status_code = http_status
    with (
        patch.object(module.anthropic, "AsyncAnthropic", return_value=_setup_mocked_generation()),
        patch.object(module, "SendGridAPIClient", return_value=sender),
        patch("app.api.deps.get_db", return_value=db),
    ):
        await module.generate_morning_briefing(
            company_id="co-1", company_name="Example",
            preferred_language="English", data_summary="fake input",
            recipient_email="admin@example.test",
        )
    sender.send.assert_called_once()
    row = db.table.return_value.insert.call_args.args[0]
    assert (row["sent_at"] is not None) == should_mark_sent
