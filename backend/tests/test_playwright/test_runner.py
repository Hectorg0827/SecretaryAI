"""Tests for PlaywrightRunner using mocked Playwright API."""
import sys
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Stub the playwright module so tests run without it installed
if "playwright" not in sys.modules:
    playwright_stub = types.ModuleType("playwright")
    async_api_stub = types.ModuleType("playwright.async_api")

    class _FakeTimeoutError(Exception):
        pass

    async_api_stub.async_playwright = AsyncMock
    async_api_stub.BrowserContext = object
    async_api_stub.PlaywrightTimeoutError = _FakeTimeoutError
    playwright_stub.async_api = async_api_stub
    sys.modules["playwright"] = playwright_stub
    sys.modules["playwright.async_api"] = async_api_stub

from app.playwright_runner.runner import PlaywrightWorkflowError  # noqa: E402


def _make_mock_playwright():
    """Return (mock_playwright, mock_browser, mock_context) with proper async setup."""
    mock_context = AsyncMock()
    mock_context.close = AsyncMock()
    mock_context.storage_state = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_playwright = AsyncMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.stop = AsyncMock()

    return mock_playwright, mock_browser, mock_context


class TestPlaywrightRunnerContextManager:
    """Verify PlaywrightRunner starts and closes cleanly without needing a real browser."""

    @pytest.mark.asyncio
    async def test_context_manager_starts_and_stops(self):
        mock_playwright, mock_browser, mock_context = _make_mock_playwright()

        # async_playwright() returns an obj whose .start() coroutine returns the playwright instance
        mock_pw_obj = MagicMock()
        mock_pw_obj.start = AsyncMock(return_value=mock_playwright)

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_obj):
            from app.playwright_runner.runner import PlaywrightRunner
            async with PlaywrightRunner({"id": "test-company"}) as runner:
                assert runner._browser is mock_browser
                assert runner._context is mock_context

        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_playwright.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_workflow_raises_error(self):
        mock_playwright, mock_browser, mock_context = _make_mock_playwright()

        mock_pw_obj = MagicMock()
        mock_pw_obj.start = AsyncMock(return_value=mock_playwright)

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_obj):
            from app.playwright_runner.runner import PlaywrightRunner
            async with PlaywrightRunner({"id": "test-company"}) as runner:
                with pytest.raises(PlaywrightWorkflowError) as exc_info:
                    await runner.run_workflow("nonexistent_workflow", {})
                assert "nonexistent_workflow" in str(exc_info.value)


class TestWorkflowRegistry:
    def test_customs_portal_registered(self):
        from app.playwright_runner.workflows import WORKFLOWS
        assert "customs_portal" in WORKFLOWS

    def test_distributor_portal_registered(self):
        from app.playwright_runner.workflows import WORKFLOWS
        assert "distributor_portal" in WORKFLOWS

    def test_all_workflows_have_name(self):
        from app.playwright_runner.workflows import WORKFLOWS
        for name, cls in WORKFLOWS.items():
            assert cls.name == name

    def test_all_workflows_have_run_method(self):
        from app.playwright_runner.workflows import WORKFLOWS
        for name, cls in WORKFLOWS.items():
            assert hasattr(cls, "run"), f"{name} workflow missing run()"
