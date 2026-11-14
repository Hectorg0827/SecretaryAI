"""Lola account memory runs under FastAPI's active event loop."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.account_memory import extract_and_save_memory


@pytest.mark.asyncio
async def test_memory_extraction_schedules_instead_of_running_a_nested_event_loop():
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=SimpleNamespace(
        content=[SimpleNamespace(text="[]")]
    ))
    with (
        patch("anthropic.AsyncAnthropic", return_value=client),
        patch("app.ai.model_router.model_for", return_value="test-model"),
        patch("asyncio.run", side_effect=AssertionError("Nested asyncio.run is prohibited")),
    ):
        extract_and_save_memory(
            MagicMock(), "company-a", "Synthetic account",
            "No durable new facts.", user_id="employee-a",
        )
        for _ in range(10):
            await asyncio.sleep(0.01)
            if client.messages.create.await_count:
                break
        assert client.messages.create.await_count == 1
