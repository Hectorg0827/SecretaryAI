"""Read-only smoke test of an already exported SecretaryAI mobile-web SPA.

Usage:
  PYTHONPATH=<local-playwright> PLAYWRIGHT_BROWSERS_PATH=<browser-cache> \
    python e2e/verify_mobile_web_spa.py <export-directory> [signin|unconfigured|outage]

The preview is served only on local loopback. All outbound requests are denied.
This does NOT validate native-device or provider-backed acceptance.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

PREVIEW = "http://127.0.0.1:8789"


def main() -> None:
    export_dir = Path(sys.argv[1]).resolve()
    expectation = sys.argv[2] if len(sys.argv) > 2 else "signin"
    assert expectation in {"signin", "unconfigured", "outage"}
    assert (export_dir / "index.html").is_file(), "Web SPA export has no index.html"

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8789", "--bind", "127.0.0.1", "--directory", str(export_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                with urlopen(PREVIEW, timeout=0.25) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Local static preview never became ready")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for width in (390, 1024):
                    context = browser.new_context(viewport={"width": width, "height": 844})
                    page_errors: list[str] = []
                    external_requests: list[str] = []
                    auth_payloads: list[dict[str, str]] = []
                    api_paths: list[str] = []

                    def guard(route):
                        url = route.request.url
                        path = urlparse(url).path
                        if url.startswith(PREVIEW):
                            route.continue_()
                        elif expectation in {"signin", "outage"} and url.startswith("https://staging.example.org/"):
                            # Explicit synthetic responses — no provider requests leave Chromium.
                            api_paths.append(path)
                            if path == "/auth/login":
                                payload = route.request.post_data_json
                                auth_payloads.append(payload)
                                if "pre_auth_token" in payload:
                                    if payload.get("totp_code") == "123456":
                                        value = {
                                            "access_token": "synthetic-test-only",
                                            "role": "owner" if expectation == "outage" else "viewer",
                                            "company_id": "fixture",
                                        }
                                        route.fulfill(status=200, content_type="application/json", body=json.dumps(value))
                                    else:
                                        route.fulfill(status=401, content_type="application/json", body=json.dumps({"detail": "Invalid fixture code"}))
                                else:
                                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                                        "requires_2fa": True, "pre_auth_token": "synthetic-pending"}))
                            elif path == "/auth/me":
                                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                                    "id": "fixture-user", "email": "tester@example.invalid", "name": "Fixture User",
                                    "role": "owner" if expectation == "outage" else "viewer",
                                    "company_id": "fixture", "company_name": "Fixture Company",
                                    "totp_enabled": True}))
                            elif path == "/api/dashboard/summary":
                                if expectation == "outage":
                                    route.fulfill(status=503, content_type="application/json", body=json.dumps({
                                        "detail": "Synthetic source unavailable"}))
                                else:
                                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                                        "generated_at": "2026-10-26T12:00:00Z"}))
                            else:
                                route.fulfill(status=503, content_type="application/json", body=json.dumps({
                                    "detail": "Endpoint deliberately unavailable in synthetic browser gate"}))
                        else:
                            external_requests.append(url)
                            route.abort()

                    context.route("**/*", guard)
                    page = context.new_page()
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    if expectation in {"signin", "outage"}:
                        try:
                            page.get_by_text("Sign in", exact=True).first.wait_for(timeout=8000)
                        except Exception as exc:
                            raise AssertionError(
                                f"Sign-in view did not render: JS errors={page_errors}; "
                                f"external={external_requests}; body={page.locator('body').inner_text()[:800]!r}"
                            ) from exc
                        assert page.locator('input[aria-label="Sign-in email address"]').count() == 1
                        assert page.locator('input[aria-label="Sign-in password"]').count() == 1
                    else:
                        try:
                            page.get_by_text("SecretaryAI server not configured").wait_for(timeout=8000)
                        except Exception as exc:
                            raise AssertionError(
                                f"Fail-closed view did not render: JS errors={page_errors}; "
                                f"external={external_requests}; body={page.locator('body').inner_text()[:800]!r}"
                            ) from exc
                        assert page.locator('input[type="password"]').count() == 0

                    assert not page_errors, f"JS errors at {width}px: {page_errors}"
                    assert not external_requests, f"Outbound request attempted: {external_requests}"
                    size = page.evaluate("({ viewport: window.innerWidth, content: document.documentElement.scrollWidth })")
                    assert size["content"] <= size["viewport"] + 2, f"Horizontal overflow: {json.dumps(size)}"
                    print(f"PASS: {expectation} at {width}px, no JS errors, external requests or overflow")

                    if expectation in {"signin", "outage"} and width == 390:
                        page.locator('input[aria-label="Sign-in email address"]').fill("tester@example.invalid")
                        page.locator('input[aria-label="Sign-in password"]').fill("fixture-password")
                        page.get_by_text("Sign in", exact=True).last.click()
                        page.get_by_text("Two-factor authentication").wait_for(timeout=10000)
                        code = page.locator('input[aria-label="Six-digit authenticator code"]')
                        if expectation == "signin":
                            code.fill("000000")
                            page.get_by_text("Verify", exact=True).click()
                            page.get_by_text("Invalid fixture code").wait_for(timeout=10000)
                        code.fill("123456")
                        page.get_by_text("Verify", exact=True).click()
                        if expectation == "outage":
                            page.get_by_text("Unavailable: Dashboard summary, Sales, Inbox, Follow-ups", exact=False).wait_for(timeout=10000)
                            page.get_by_text("Dashboard source freshness not verified").wait_for(timeout=10000)
                            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                            assert page.get_by_text("Revenue (30 days)").count() == 0
                            assert {"/api/dashboard/summary", "/api/dashboard/emails", "/api/dashboard/notes"}.issubset(set(api_paths))
                            assert "/api/dashboard/sales" in api_paths
                            assert not page_errors, f"Owner outage view JS errors: {page_errors}"
                            assert not external_requests, f"Owner outage outbound requests: {external_requests}"
                            print("PASS: owner dashboard fails closed during synthetic source outage")
                            context.close()
                            continue
                        page.get_by_text("Your role-limited workspace").wait_for(timeout=10000)
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                        assert [sorted(payload.keys()) for payload in auth_payloads] == [
                            ["email", "password"], ["pre_auth_token", "totp_code"],
                            ["pre_auth_token", "totp_code"],
                        ], f"Unexpected login wire contracts: {auth_payloads!r}"
                        page.get_by_role("button", name="Account and security settings").click()
                        page.get_by_text("Push notifications").wait_for(timeout=10000)
                        page.get_by_text("Sign out", exact=True).wait_for(timeout=10000)
                        # A viewer must never ask for leader-only financial,
                        # inbox, or follow-up data, even when the server is mocked.
                        assert not ({
                            "/api/dashboard/sales", "/api/dashboard/emails",
                            "/api/dashboard/notes", "/api/actions/pending",
                        } & set(api_paths)), f"Restricted paths requested: {api_paths!r}"

                        # Browser and native confirmation are one shared modal.
                        # Cancellation must keep the user signed in.
                        page.get_by_text("Sign out", exact=True).click()
                        page.get_by_text("Sign out of SecretaryAI?").wait_for(timeout=10000)
                        page.get_by_role("button", name="Cancel sign out").click()
                        assert page.get_by_text("Push notifications").is_visible()
                        page.get_by_text("Sign out of SecretaryAI?").wait_for(state="hidden", timeout=10000)

                        page.get_by_text("Sign out", exact=True).click()
                        page.get_by_role("button", name="Confirm sign out").click()
                        page.locator('input[aria-label="Sign-in password"]').wait_for(timeout=10000)
                        # Logout removes the memory-only token, and a tab reload
                        # must not silently restore an authenticated session.
                        assert page.evaluate("Object.keys(localStorage).filter(k => /token|secretary_access/i.test(k))") == []
                        page.reload(wait_until="domcontentloaded")
                        page.locator('input[aria-label="Sign-in password"]').wait_for(timeout=10000)
                        assert not page_errors, f"MFA navigation errors: {page_errors}"
                        assert not external_requests, f"Unexpected live requests: {external_requests}"
                        print("PASS: synthetic MFA retry, viewer scope, sign-out cancellation, token clearing and reload")
                    context.close()
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
