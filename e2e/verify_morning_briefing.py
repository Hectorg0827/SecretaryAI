"""Synthetic-only browser gate for Chat 5's web morning briefing.

Requires built local Vite preview on http://127.0.0.1:4173 and Python Playwright
with Chromium installed. Never contacts staging, production, or live providers.
The unsigned role tokens are UI fixtures ONLY, not authentication evidence.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

PREVIEW = "http://127.0.0.1:4173"
SYNTHETIC_TIME = "2026-10-26T12:00:00Z"


def _fake_role_token(role: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": "synthetic-ui-tester", "role": role, "company_id": "synthetic-tenant",
    }).encode()).decode().rstrip("=")
    return "unsigned." + payload + ".synthetic-only"


def _respond(route, *, summary_error: bool = False, summary_malformed: bool = False,
             approvals_malformed: bool = False, briefing_malformed: bool = False,
             dashboard_aux_malformed: bool = False,
             role: str = "owner",
             email_fixture: bool = False, work_error: bool = False,
             requests_seen: list[str] | None = None,
             auth_fields_seen: list[tuple[str, ...]] | None = None) -> None:
    path = urlparse(route.request.url).path
    if requests_seen is not None:
        requests_seen.append(path)
    if path in ("/auth/login", "/auth/register"):
        body = route.request.post_data_json
        if auth_fields_seen is not None:
            auth_fields_seen.append(tuple(sorted(body.keys())))
        if path == "/auth/register":
            result = {"access_token": _fake_role_token("owner"), "company_id": "synthetic-tenant", "role": "owner"}
            route.fulfill(status=201, content_type="application/json", body=json.dumps(result))
            return
        if "pre_auth_token" in body:
            if body["pre_auth_token"] == "synthetic-pending" and body.get("totp_code") == "123456":
                result = {"access_token": _fake_role_token("owner"), "company_id": "synthetic-tenant", "role": "owner"}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(result))
            else:
                route.fulfill(status=401, content_type="application/json", body=json.dumps({"detail": "Invalid synthetic 2FA code"}))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "requires_2fa": True, "pre_auth_token": "synthetic-pending",
        }))
        return
    if summary_error and path == "/api/dashboard/summary":
        route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Synthetic outage"}))
        return
    if summary_malformed and path == "/api/dashboard/summary":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "accounts": {"healthy": 8}, "inventory_alerts": [],
            "generated_at": SYNTHETIC_TIME,
        }))
        return
    if approvals_malformed and path == "/api/actions/pending":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"drafts": None}))
        return
    if briefing_malformed and path in (
        "/api/dashboard/sales", "/api/dashboard/notes", "/api/feed/", "/api/admin/audit-log",
    ):
        invalid = {
            "/api/dashboard/sales": {"total_revenue": "0", "order_count": 0},
            "/api/dashboard/notes": {"notes": [{"id": "incomplete", "done": "false"}]},
            "/api/feed/": {"events": [None]},
            "/api/admin/audit-log": {"events": [{"id": "unverified", "event_type": "draft_approved",
                                                   "created_at": "not-a-date"}]},
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(invalid[path]))
        return
    if dashboard_aux_malformed and path in ("/api/dashboard/emails", "/api/workflows/"):
        invalid = {
            "/api/dashboard/emails": {"emails": [None]},
            "/api/workflows/": {"workflows": [{"run_id": "synthetic-run", "status": "running",
                                                "workflow_name": "fixture", "total_steps": 0}]},
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(invalid[path]))
        return
    if work_error and path in ("/api/actions/pending", "/api/dashboard/notes", "/api/inventory/alerts"):
        route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Synthetic source outage"}))
        return
    fixtures = {
        "/api/dashboard/summary": {
            "accounts": {"healthy": 8, "slowing": 2, "at_risk": 1, "dormant": 1},
            "inventory_alerts": [{"item_id": "synthetic-sku", "product_name": "QA sample",
                                  "stock_status": "critical", "total_qty": 0, "weeks_remaining": 0, "needs_po": True}],
            "pending_actions": 1, "unread_emails": 0, "generated_at": SYNTHETIC_TIME, "cached": True,
        },
        "/api/dashboard/sales": {"total_revenue": 1200, "order_count": 3, "avg_order_value": 400,
                                  "top_account": "Synthetic client", "vs_prior_period_pct": 0, "chart_data": []},
        "/api/dashboard/notes": {"notes": [{"id": "synthetic-note", "text": "Fixture follow-up",
                                         "done": False, "due_date": "2026-10-26", "created_at": SYNTHETIC_TIME}]},
        "/api/feed/": {"events": [{"id": "synthetic-event", "event_type": "opportunity",
                                     "title": "Synthetic opportunity (QA only)", "body": "Fixture content",
                                     "priority": "low", "action_type": "opportunity", "action_label": None,
                                     "action_data": {}, "entity_name": None, "is_read": False,
                                     "created_at": SYNTHETIC_TIME}], "unread_count": 1},
        "/api/actions/pending": {"drafts": [{"id": "synthetic-draft", "status": "pending",
                                                 "action_type": "draft_purchase_order", "content": {},
                                                 "created_at": SYNTHETIC_TIME}]},
        "/api/admin/audit-log": {"events": [{"id": "synthetic-audit", "event_type": "draft_approved",
                                                "created_at": SYNTHETIC_TIME, "metadata": {"execution_status": "queued"}}],
                                  "total": 1, "page": 1, "page_size": 25, "pages": 1},
        "/api/dashboard/emails": {"emails": ([{
            "id": "synthetic-email", "thread_id": "synthetic-thread", "from": "Synthetic contact",
            "from_email": "synthetic@example.invalid", "subject": "Synthetic question",
            "snippet": "Fixture", "ai_summary": "Fixture only", "ai_priority": "high",
            "ai_action_needed": "Review", "received_at": SYNTHETIC_TIME,
            "is_read": False, "labels": [],
        }] if email_fixture else [])},
        "/api/dashboard/emails/draft-reply": {"draft": "Synthetic draft — never send", "subject": "Synthetic reply"},
        "/api/inventory/alerts": {"alerts": []},
        "/api/workflows/": {"workflows": []},
        "/api/inbox": {"items": [], "unread_count": 0, "total_count": 0},
        "/api/agent/status": {"connected": False, "last_seen": None},
        "/api/connectors/status": {"connectors": []},
        "/api/settings/integrations": {
            "quickbooks_online": {"connected": False},
            "quickbooks_desktop": {"connected": False},
            "gmail": {"connected": False},
            "google_sheets": {"connected": False},
        },
        "/api/billing/subscription": {
            "subscription": {"status": "active", "current_period_end": "2026-12-31T00:00:00Z",
                             "cancel_at_period_end": False},
            "plan": {"name": "Synthetic QA Plan"},
        },
        "/auth/me": {"name": "Synthetic Tester", "role": role, "totp_enabled": False},
    }
    if path not in fixtures:
        # Fail closed: never respond with plausible empty business data to an
        # unknown endpoint or allow a request to reach an actual backend.
        route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Unspecified fixture"}))
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps(fixtures[path]))


def _context(browser, role: str, width: int, *, summary_error: bool = False,
             summary_malformed: bool = False, approvals_malformed: bool = False,
             briefing_malformed: bool = False, dashboard_aux_malformed: bool = False,
             email_fixture: bool = False, work_error: bool = False,
             requests_seen: list[str] | None = None,
             authenticated: bool = True,
             auth_fields_seen: list[tuple[str, ...]] | None = None):
    context = browser.new_context(viewport={"width": width, "height": 844 if width < 768 else 900})
    if authenticated:
        context.add_init_script(f"localStorage.setItem('secretary_token', {json.dumps(_fake_role_token(role))});")
    def mock_route(route):
        _respond(route, summary_error=summary_error, summary_malformed=summary_malformed,
                 approvals_malformed=approvals_malformed,
                 briefing_malformed=briefing_malformed,
                 dashboard_aux_malformed=dashboard_aux_malformed, role=role,
                 email_fixture=email_fixture, work_error=work_error,
                 requests_seen=requests_seen, auth_fields_seen=auth_fields_seen)
    context.route("**/api/**", mock_route)
    context.route("**/auth/**", mock_route)
    return context


def main() -> None:
    if os.environ.get("E2E_WEB_URL", PREVIEW) != PREVIEW:
        raise SystemExit("This synthetic suite runs ONLY on the local preview")
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    if not (frontend / "dist" / "index.html").is_file():
        raise SystemExit("Build frontend/dist before running browser acceptance")
    # Launch preview as a child so it shares the test process' network sandbox.
    server = subprocess.Popen(
        ["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", "4173", "--strictPort"],
        cwd=frontend, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            if server.poll() is not None:
                raise RuntimeError("Local Vite preview exited before accepting connections")
            try:
                with urlopen(PREVIEW, timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Local Vite preview not ready")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (390, 1440):
                    context = _context(browser, "owner", width)
                    try:
                        page = context.new_page()
                        page.goto(PREVIEW, wait_until="domcontentloaded")
                        for name in ("1 · Red flags", "2 · Yellow watchlist", "3 · Opportunities",
                                     "4 · Scoreboard", "5 · Today’s decisions", "6 · Actions & failures"):
                            page.get_by_role("heading", name=name).wait_for()
                        page.get_by_role("region", name="3 · Opportunities").get_by_text("Synthetic opportunity (QA only)").wait_for()
                        page.get_by_text("queued", exact=False).first.wait_for()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
                    finally:
                        context.close()

                restricted = _context(browser, "sales_rep", 390)
                try:
                    page = restricted.new_page()
                    page.goto(PREVIEW + "/accounts", wait_until="domcontentloaded")
                    page.wait_for_url(PREVIEW + "/")
                    assert page.get_by_text("Your five-minute morning briefing").count() == 0
                    page.get_by_text("awaiting server-side assignment verification").wait_for()
                finally:
                    restricted.close()

                outage = _context(browser, "owner", 390, summary_error=True)
                try:
                    page = outage.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    page.get_by_role("alert").get_by_text("Dashboard summary unavailable", exact=False).wait_for()
                    assert page.get_by_text("0 at-risk accounts").count() == 0
                finally:
                    outage.close()

                # HTTP 200 is not proof of business data completeness. Invalid
                # counts must trigger unavailable evidence, not reassuring zeros.
                incomplete = _context(browser, "owner", 390, summary_malformed=True)
                try:
                    page = incomplete.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    page.get_by_role("alert").get_by_text("Dashboard summary unavailable", exact=False).wait_for()
                    assert page.get_by_text("0 at-risk accounts").count() == 0
                    assert page.get_by_text("All products at healthy levels").count() == 0
                finally:
                    incomplete.close()

                malformed_approvals = _context(browser, "owner", 390, approvals_malformed=True)
                try:
                    page = malformed_approvals.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    page.get_by_role("alert").get_by_text("Approvals source unavailable", exact=False).wait_for()
                    assert page.get_by_text("Nothing waiting for approval").count() == 0
                    assert page.get_by_role("region", name="5 · Today’s decisions").get_by_text("Source unavailable", exact=False).count() > 0
                finally:
                    malformed_approvals.close()

                # A well-formed top-level array can still contain malformed
                # rows. Reject each affected read independently while leaving
                # valid approvals and dashboard inventory evidence visible.
                malformed_requests: list[str] = []
                malformed_digest = _context(browser, "owner", 390, briefing_malformed=True,
                                            requests_seen=malformed_requests)
                try:
                    page = malformed_digest.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    for title in ("3 · Opportunities", "4 · Scoreboard", "6 · Actions & failures"):
                        page.get_by_role("region", name=title).get_by_text("Source unavailable", exact=False).wait_for()
                    decisions = page.get_by_role("region", name="5 · Today’s decisions")
                    decisions.get_by_text("Source unavailable", exact=False).wait_for()
                    decisions.get_by_text("1 drafts waiting for human approval", exact=False).wait_for()
                    page.get_by_role("region", name="1 · Red flags").get_by_text("1 at-risk accounts").wait_for()
                    for warning in ("Sales analytics source unavailable", "Insights source unavailable",
                                    "Follow-up source unavailable"):
                        assert page.get_by_role("alert").get_by_text(warning, exact=False).count() > 0
                    assert page.get_by_text("Something went wrong").count() == 0
                    assert {"/api/dashboard/sales", "/api/dashboard/notes", "/api/feed/",
                            "/api/admin/audit-log"}.issubset(set(malformed_requests))
                    assert page.get_by_text("$0.00", exact=True).count() == 0
                finally:
                    malformed_digest.close()

                # Malformed inbox/workflow HTTP 200 bodies must not crash the
                # owner dashboard or turn an unreadable source into a clean slate.
                malformed_aux = _context(browser, "owner", 390, dashboard_aux_malformed=True)
                try:
                    page = malformed_aux.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    for warning in ("Email source unavailable", "Workflow source unavailable"):
                        page.get_by_role("alert").get_by_text(warning, exact=False).wait_for()
                    page.get_by_role("heading", name="Your five-minute morning briefing").wait_for()
                    assert page.get_by_text("No unread emails — inbox is clear.").count() == 0
                    assert page.get_by_text("Something went wrong").count() == 0
                finally:
                    malformed_aux.close()

                # Semantic links in the six-card briefing support keyboard-only
                # navigation into the unified inbox, without authorizing action.
                keyboard_context = _context(browser, "owner", 390)
                try:
                    page = keyboard_context.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    approval_link = page.get_by_role("link", name="Open approval inbox")
                    approval_link.focus()
                    assert approval_link.evaluate("element => document.activeElement === element")
                    page.keyboard.press("Enter")
                    page.wait_for_url(PREVIEW + "/inbox")
                    # Route URL can update before React finishes mounting Inbox.
                    page.get_by_role("button", name="Approvals").wait_for()
                finally:
                    keyboard_context.close()

                # Direct email sending cannot be reachable from dashboard draft UX.
                email_paths: list[str] = []
                email_context = _context(browser, "owner", 390, email_fixture=True, requests_seen=email_paths)
                try:
                    page = email_context.new_page()
                    page.goto(PREVIEW, wait_until="domcontentloaded")
                    page.get_by_role("button", name="Draft reply", exact=True).click()
                    page.get_by_role("button", name="Copy draft (do not send)").wait_for()
                    assert page.get_by_role("button", name="Send Reply").count() == 0
                    page.get_by_role("button", name="Copy draft (do not send)").click()
                    assert "/api/dashboard/emails/send-reply" not in email_paths
                finally:
                    email_context.close()

                work_context = _context(browser, "owner", 390, work_error=True)
                try:
                    page = work_context.new_page()
                    page.goto(PREVIEW + "/work", wait_until="domcontentloaded")
                    page.get_by_role("alert").get_by_text("Work data is incomplete", exact=False).wait_for()
                    assert page.get_by_text("All clear", exact=True).count() == 0
                finally:
                    work_context.close()

                inbox_context = _context(browser, "owner", 390)
                try:
                    page = inbox_context.new_page()
                    inbox_context.route("**/api/inbox", lambda route: route.fulfill(status=503, body='{"detail":"outage"}', content_type="application/json"))
                    page.goto(PREVIEW + "/inbox", wait_until="domcontentloaded")
                    page.get_by_role("alert").get_by_text("Inbox data could not be loaded", exact=False).wait_for()
                    assert page.get_by_text("All caught up").count() == 0
                finally:
                    inbox_context.close()

                # Setup and billing are read-only, source-aware and scoped to owners.
                billing_paths: list[str] = []
                setup_context = _context(browser, "owner", 390, requests_seen=billing_paths)
                try:
                    page = setup_context.new_page()
                    page.goto(PREVIEW + "/settings?qbo=connected", wait_until="domcontentloaded")
                    page.get_by_text("Not connected", exact=True).first.wait_for()
                    assert page.get_by_text("QuickBooks Online connected!", exact=False).count() == 0
                    assert page.get_by_role("button", name="Save Changes").count() == 0
                    page.get_by_role("button", name="Billing").click()
                    page.get_by_text("Synthetic QA Plan").wait_for()
                    page.get_by_text("Read-only account billing", exact=False).wait_for()
                    assert page.get_by_role("button", name="Checkout").count() == 0
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
                    assert all(path not in billing_paths for path in ("/api/billing/checkout", "/api/billing/portal"))
                finally:
                    setup_context.close()

                manager_context = _context(browser, "manager", 390)
                try:
                    page = manager_context.new_page()
                    page.goto(PREVIEW + "/settings", wait_until="domcontentloaded")
                    assert page.get_by_role("button", name="Billing").count() == 0
                    page.goto(PREVIEW + "/connectors", wait_until="domcontentloaded")
                    page.get_by_text("No connectors registered yet.").wait_for()
                    assert page.get_by_role("button", name="Rotate Install Secret").count() == 0
                finally:
                    manager_context.close()

                connector_context = _context(browser, "owner", 390)
                try:
                    page = connector_context.new_page()
                    connector_context.route("**/api/connectors/status", lambda route: route.fulfill(status=503, body='{"detail":"outage"}', content_type="application/json"))
                    page.goto(PREVIEW + "/connectors", wait_until="domcontentloaded")
                    page.get_by_role("alert").get_by_text("Connector status is unavailable", exact=False).wait_for()
                    assert page.get_by_text("No connectors registered yet.").count() == 0
                finally:
                    connector_context.close()

                # Synthetic signup and staged MFA: the second request must not resend a password.
                auth_fields: list[tuple[str, ...]] = []
                login_context = _context(browser, "owner", 390, authenticated=False, auth_fields_seen=auth_fields)
                try:
                    page = login_context.new_page()
                    page.goto(PREVIEW + "/work", wait_until="domcontentloaded")
                    page.wait_for_url(PREVIEW + "/login")
                    page.get_by_label("Email address").fill("mfa@example.invalid")
                    page.get_by_label("Password", exact=True).fill("SyntheticPassword9")
                    page.get_by_role("button", name="Sign in").click()
                    page.get_by_role("heading", name="Two-factor authentication").wait_for()
                    assert page.evaluate("localStorage.getItem('secretary_token')") is None
                    page.get_by_label("Authenticator code").fill("000000")
                    page.get_by_role("button", name="Verify code").click()
                    page.get_by_role("alert").get_by_text("Invalid synthetic 2FA code").wait_for()
                    page.get_by_label("Authenticator code").fill("123456")
                    page.get_by_role("button", name="Verify code").click()
                    page.wait_for_url(PREVIEW + "/work")
                    assert ("email", "password") == auth_fields[0]
                    assert auth_fields[1:] == [("pre_auth_token", "totp_code"), ("pre_auth_token", "totp_code")]
                finally:
                    login_context.close()

                registration_fields: list[tuple[str, ...]] = []
                register_context = _context(browser, "owner", 390, authenticated=False, auth_fields_seen=registration_fields)
                try:
                    page = register_context.new_page()
                    page.goto(PREVIEW + "/register", wait_until="domcontentloaded")
                    page.get_by_label("Company name").fill("Synthetic QA Tenant")
                    page.get_by_label("Your full name").fill("Fixture User")
                    page.get_by_label("Work email").fill("fixture@example.invalid")
                    page.get_by_label("Password", exact=False).first.fill("weakpass")
                    page.get_by_label("Confirm password").fill("weakpass")
                    assert page.get_by_role("button", name="Create account").is_disabled()
                    assert registration_fields == []
                    page.get_by_label("Password", exact=False).first.fill("SyntheticPassword9")
                    page.get_by_label("Confirm password").fill("SyntheticPassword9")
                    page.get_by_role("button", name="Create account").click()
                    page.wait_for_url(PREVIEW + "/")
                    assert registration_fields == [("company_name", "email", "full_name", "password")]
                finally:
                    register_context.close()

                mfa_status_context = _context(browser, "owner", 390)
                try:
                    page = mfa_status_context.new_page()
                    mfa_status_context.route("**/auth/me", lambda route: route.fulfill(status=503, body='{"detail":"outage"}', content_type="application/json"))
                    page.goto(PREVIEW + "/settings", wait_until="domcontentloaded")
                    page.get_by_role("button", name="Security").click()
                    page.get_by_role("button", name="MFA status unavailable — retry").wait_for()
                    assert page.get_by_role("button", name="Enable").count() == 0
                finally:
                    mfa_status_context.close()

                mfa_enabled_context = _context(browser, "owner", 390)
                try:
                    page = mfa_enabled_context.new_page()
                    mfa_enabled_context.route("**/auth/me", lambda route: route.fulfill(status=200, body='{"role":"owner","totp_enabled":true}', content_type="application/json"))
                    page.goto(PREVIEW + "/settings", wait_until="domcontentloaded")
                    page.get_by_role("button", name="Security").click()
                    page.get_by_text("Active", exact=True).wait_for()
                    assert page.get_by_role("button", name="Enable").count() == 0
                finally:
                    mfa_enabled_context.close()
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    print("PASS: synthetic-only briefing, role restrictions, MFA/signup, inbox/setup outages and billing read-only")


if __name__ == "__main__":
    main()
