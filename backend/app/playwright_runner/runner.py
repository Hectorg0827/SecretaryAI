import logging
import os
from pathlib import Path
from typing import Optional

from app.playwright_runner.workflows import WORKFLOWS

log = logging.getLogger(__name__)

STATE_DIR = Path(".playwright_state")


class PlaywrightWorkflowError(Exception):
    def __init__(self, workflow_name: str, reason: str):
        self.workflow_name = workflow_name
        self.reason = reason
        super().__init__(f"Workflow '{workflow_name}' failed: {reason}")


class PlaywrightRunner:
    def __init__(self, company_config: dict):
        self._config = company_config
        company_id = company_config.get("id", "default")
        STATE_DIR.mkdir(exist_ok=True)
        self._state_path = str(STATE_DIR / f"{company_id}.json")
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None

    async def __aenter__(self) -> "PlaywrightRunner":
        from playwright.async_api import async_playwright, PlaywrightTimeoutError  # noqa: F401

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

        storage_state = self._state_path if os.path.exists(self._state_path) else None
        self._context = await self._browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 800},
        )
        return self

    async def __aexit__(self, *_):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def run_workflow(self, workflow_name: str, params: dict) -> dict:
        workflow_cls = WORKFLOWS.get(workflow_name)
        if workflow_cls is None:
            raise PlaywrightWorkflowError(workflow_name, f"Unknown workflow '{workflow_name}'")

        page = await self._context.new_page()
        page.set_default_timeout(30_000)
        try:
            from playwright.async_api import PlaywrightTimeoutError
            workflow = workflow_cls()
            result = await workflow.run(page, params)
            await self.save_auth_state()
            return result
        except Exception as exc:
            # Catch PlaywrightTimeoutError by name to avoid import at module level
            if "PlaywrightTimeoutError" in type(exc).__name__ or "TimeoutError" in type(exc).__name__:
                raise PlaywrightWorkflowError(workflow_name, f"Timeout: {exc}") from exc
            raise PlaywrightWorkflowError(workflow_name, str(exc)) from exc
        finally:
            await page.close()

    async def capture_page(self, url: str, selector: str = None) -> str:
        page = await self._context.new_page()
        page.set_default_timeout(30_000)
        try:
            await page.goto(url, wait_until="networkidle")
            if selector:
                el = await page.query_selector(selector)
                return await el.inner_html() if el else ""
            return await page.content()
        finally:
            await page.close()

    async def extract_table(self, url: str, table_selector: str) -> list[dict]:
        page = await self._context.new_page()
        page.set_default_timeout(30_000)
        try:
            await page.goto(url, wait_until="networkidle")
            rows = await page.query_selector_all(f"{table_selector} tr")
            headers: list[str] = []
            result: list[dict] = []
            for i, row in enumerate(rows):
                cells = await row.query_selector_all("th, td")
                texts = [await c.inner_text() for c in cells]
                if i == 0:
                    headers = [t.strip() for t in texts]
                elif texts:
                    result.append(dict(zip(headers, [t.strip() for t in texts])))
            return result
        finally:
            await page.close()

    async def download_file(self, url: str, dest_path: str) -> str:
        page = await self._context.new_page()
        page.set_default_timeout(30_000)
        try:
            async with page.expect_download() as download_info:
                await page.goto(url)
            download = await download_info.value
            await download.save_as(dest_path)
            return dest_path
        finally:
            await page.close()

    async def save_auth_state(self) -> None:
        if self._context:
            await self._context.storage_state(path=self._state_path)
