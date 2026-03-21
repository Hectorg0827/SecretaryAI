"""
Tests for the model router tier system.

Verifies:
  1. Every registered task maps to the correct tier (Haiku vs Sonnet)
  2. Haiku-tier tasks are immune to CLAUDE_MODEL operator overrides
  3. Sonnet-tier tasks respect CLAUDE_MODEL version pin
  4. Unknown tasks fall back to Sonnet
  5. secretary.py callers pass the right model to the Anthropic client
  6. morning_briefing.py passes Haiku model
  7. weekly_report.py passes Sonnet model
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure env vars exist before any app import
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


# ─── Model tier constants ──────────────────────────────────────────────────────

from app.ai.model_router import HAIKU, SONNET

HAIKU_TASKS  = ["classify_intent", "email_priority_batch", "morning_briefing",
                "stock_alert_message", "detect_action_proposal"]
SONNET_TASKS = ["chat", "stream_chat", "weekly_report",
                "draft_email_reply", "demand_forecast", "anomaly_analysis"]


# ─── Tier mapping ──────────────────────────────────────────────────────────────

class TestTierMapping:
    def test_haiku_tasks_return_haiku(self):
        from app.ai.model_router import model_for
        for task in HAIKU_TASKS:
            assert model_for(task) == HAIKU, \
                f"Task '{task}' should use Haiku but got {model_for(task)!r}"

    def test_sonnet_tasks_return_sonnet_by_default(self):
        from app.ai.model_router import model_for
        for task in SONNET_TASKS:
            result = model_for(task)
            assert result == SONNET, \
                f"Task '{task}' should use Sonnet but got {result!r}"

    def test_unknown_task_falls_back_to_sonnet(self):
        from app.ai.model_router import model_for
        assert model_for("completely_unknown_task") == SONNET

    def test_empty_string_task_falls_back_to_sonnet(self):
        from app.ai.model_router import model_for
        assert model_for("") == SONNET


# ─── Operator version pin ──────────────────────────────────────────────────────

class TestOperatorVersionPin:
    def test_sonnet_task_respects_claude_model_env_var(self):
        """Operator can pin to a different Sonnet release."""
        from app.config import get_settings
        pinned = "claude-sonnet-4-5"
        with patch.object(get_settings(), "claude_model", pinned):
            with patch("app.ai.model_router.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(claude_model=pinned)
                from app.ai.model_router import model_for
                result = model_for("chat")
        assert result == pinned

    def test_haiku_task_ignores_claude_model_env_var(self):
        """Haiku tasks must NOT be affected by CLAUDE_MODEL, even if set to Opus."""
        with patch("app.ai.model_router.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(claude_model="claude-opus-4-6")
            from app.ai.model_router import model_for
            result = model_for("classify_intent")
        assert result == HAIKU, \
            "classify_intent must always use Haiku regardless of CLAUDE_MODEL"

    def test_haiku_task_ignores_sonnet_pin(self):
        """Even if CLAUDE_MODEL is a Sonnet pin, Haiku tasks stay on Haiku."""
        with patch("app.ai.model_router.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(claude_model="claude-sonnet-4-5")
            from app.ai.model_router import model_for
            for task in HAIKU_TASKS:
                assert model_for(task) == HAIKU, \
                    f"Haiku task '{task}' should not be overridden by CLAUDE_MODEL"

    def test_sonnet_task_uses_default_when_claude_model_matches_sonnet(self):
        """If CLAUDE_MODEL is already the default Sonnet, no override occurs."""
        with patch("app.ai.model_router.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(claude_model=SONNET)
            from app.ai.model_router import model_for
            assert model_for("chat") == SONNET


# ─── secretary.py model passthrough ───────────────────────────────────────────

class TestSecretaryModelPassthrough:
    @pytest.mark.asyncio
    async def test_classify_intent_uses_haiku(self):
        """classify_intent must call the Anthropic client with the Haiku model."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="inventory_check")]

        with patch("app.ai.secretary._client") as mock_client:
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            from app.ai.secretary import classify_intent
            await classify_intent("How much Widget A do we have?")

        call_kwargs = mock_client.messages.create.call_args
        used_model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert used_model == HAIKU, \
            f"classify_intent must use Haiku, got {used_model!r}"

    @pytest.mark.asyncio
    async def test_chat_uses_sonnet(self):
        """chat() must call the Anthropic client with the Sonnet model."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Here is your answer.")]

        with patch("app.ai.secretary._client") as mock_client:
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            from app.ai.secretary import chat
            await chat(
                user_message="Give me a sales summary.",
                conversation_history=[],
                data_summary="Sales: $50k",
                company_context={"company_name": "Acme", "business_type": "distributor",
                                 "user_role": "owner", "timezone": "UTC",
                                 "preferred_language": "English"},
            )

        call_kwargs = mock_client.messages.create.call_args
        used_model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert used_model == SONNET, \
            f"chat must use Sonnet, got {used_model!r}"

    @pytest.mark.asyncio
    async def test_stream_chat_uses_sonnet(self):
        """stream_chat() must open a stream with the Sonnet model."""
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.text_stream = _async_iter(["Hello ", "world"])

        with patch("app.ai.secretary._client") as mock_client:
            mock_client.messages.stream = MagicMock(return_value=mock_stream)
            from app.ai.secretary import stream_chat
            chunks = []
            async for chunk in stream_chat(
                user_message="Morning summary.",
                conversation_history=[],
                data_summary="All good.",
                company_context={"company_name": "Acme", "business_type": "distributor",
                                 "user_role": "owner", "timezone": "UTC",
                                 "preferred_language": "English"},
            ):
                chunks.append(chunk)

        call_kwargs = mock_client.messages.stream.call_args
        used_model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert used_model == SONNET, \
            f"stream_chat must use Sonnet, got {used_model!r}"
        assert "".join(chunks) == "Hello world"


# ─── morning_briefing.py model passthrough ────────────────────────────────────

class TestMorningBriefingModel:
    @pytest.mark.asyncio
    async def test_morning_briefing_uses_haiku(self):
        """generate_morning_briefing must call Anthropic with the Haiku model."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Good morning!")]
        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("app.scheduler.morning_briefing.anthropic.AsyncAnthropic",
                   return_value=mock_client):
            from app.scheduler.morning_briefing import generate_morning_briefing
            await generate_morning_briefing(
                company_id="co-1",
                company_name="Acme",
                preferred_language="English",
                data_summary="Revenue: $50k",
                recipient_email="admin@acme.com",
            )

        call_kwargs = mock_client.messages.create.call_args
        used_model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert used_model == HAIKU, \
            f"morning_briefing must use Haiku, got {used_model!r}"


# ─── weekly_report.py model passthrough ───────────────────────────────────────

class TestWeeklyReportModel:
    @pytest.mark.asyncio
    async def test_weekly_report_uses_sonnet(self):
        """generate_weekly_report must call Anthropic with the Sonnet model."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Weekly report text.")]
        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("app.scheduler.weekly_report.anthropic.AsyncAnthropic",
                   return_value=mock_client):
            from app.scheduler.weekly_report import generate_weekly_report
            await generate_weekly_report(
                company_id="co-1",
                company_name="Acme",
                preferred_language="English",
                data_summary="Sales: $200k. 3 at-risk accounts.",
            )

        call_kwargs = mock_client.messages.create.call_args
        used_model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert used_model == SONNET, \
            f"weekly_report must use Sonnet, got {used_model!r}"


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _async_iter(items):
    for item in items:
        yield item
