"""Real browser signup -> dashboard -> logout-local -> login acceptance test.

Requires a reachable staging web frontend + API, never mocked. This script
fails (not skips) if configuration is missing. It creates a new test tenant.
"""
import os
import uuid
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main() -> None:
    base = os.environ.get("E2E_WEB_URL", "").rstrip("/")
    domain = os.environ.get("E2E_EMAIL_DOMAIN", "")
    if not base.startswith("https://") or not domain or "." not in domain:
        raise SystemExit("E2E_WEB_URL (HTTPS) and E2E_EMAIL_DOMAIN are required")
    email = f"secretary-e2e-{uuid.uuid4().hex[:12]}@{domain}"
    password = "E2e!" + uuid.uuid4().hex + "7"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/register", wait_until="domcontentloaded", timeout=30000)
        page.get_by_label("Company name").fill("E2E Acceptance " + uuid.uuid4().hex[:8])
        page.get_by_label("Your full name").fill("E2E Tester")
        page.get_by_label("Work email").fill(email)
        page.locator("#password").fill(password)
        page.get_by_label("Confirm password").fill(password)
        with page.expect_response(lambda r: r.url.endswith("/auth/register"), timeout=30000) as reg:
            page.get_by_role("button", name="Create account").click()
        assert reg.value.status == 201, f"Signup HTTP {reg.value.status}"
        page.wait_for_url(lambda url: urlparse(url).path == "/", timeout=30000)
        assert page.get_by_text("Dashboard", exact=True).first.is_visible()
        # Force a fresh sign-in, no retained JWT from signup.
        page.evaluate("localStorage.clear()")
        page.goto(base + "/login", wait_until="domcontentloaded", timeout=30000)
        page.get_by_label("Email address").fill(email)
        page.locator("#password").fill(password)
        with page.expect_response(lambda r: r.url.endswith("/auth/login"), timeout=30000) as login:
            page.get_by_role("button", name="Sign in").click()
        assert login.value.status == 200, f"Login HTTP {login.value.status}"
        page.wait_for_url(lambda url: urlparse(url).path == "/", timeout=30000)
        assert page.get_by_text("Dashboard", exact=True).first.is_visible()
        assert page.evaluate("localStorage.getItem('secretary_token')")  # real JWT stored
        browser.close()
    print("PASS: live browser signup, dashboard navigation and fresh signin")


if __name__ == "__main__":
    main()
