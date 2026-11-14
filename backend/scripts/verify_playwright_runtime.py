"""Build-time/runtime self-check for the browser used by portal automation.

No company data, credentials, remote URL, or production service is accessed.
Runs inside backend Docker image AFTER switching to the non-root app user.
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def verify() -> None:
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("Chromium runtime verification must run as a non-root user")

    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browser_path or not Path(browser_path).is_dir():
        raise RuntimeError("PLAYWRIGHT_BROWSERS_PATH must point to installed browser files")

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.is_file():
            raise RuntimeError("Playwright Chromium executable was not installed")

        browser = playwright.chromium.launch(headless=True, timeout=20_000)
        try:
            page = browser.new_page(viewport={"width": 800, "height": 480})
            # Never navigate to the public Internet or send user data.
            page.set_content(
                "<!doctype html><html><body><main id='ready'>Browser ready</main></body></html>",
                timeout=10_000,
            )
            if page.locator("#ready").inner_text() != "Browser ready":
                raise RuntimeError("Chromium did not render the local probe")
            screenshot = page.screenshot(timeout=10_000)
            if not screenshot.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
                raise RuntimeError("Chromium screenshot output is invalid")
            print("PASS: non-root Chromium launch, local page render and PNG capture")
        finally:
            browser.close()


if __name__ == "__main__":
    verify()
