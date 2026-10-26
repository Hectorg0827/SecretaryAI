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


def _respond(route, *, summary_error: bool = False, role: str = "owner") -> None:
    path = urlparse(route.request.url).path
    if summary_error and path == "/api/dashboard/summary":
        route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Synthetic outage"}))
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
        "/api/dashboard/emails": {"emails": []},
        "/api/agent/status": {"connected": False, "last_seen": None},
        "/auth/me": {"name": "Synthetic Tester", "role": role},
    }
    if path not in fixtures:
        # Fail closed: never respond with plausible empty business data to an
        # unknown endpoint or allow a request to reach an actual backend.
        route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Unspecified fixture"}))
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps(fixtures[path]))


def _context(browser, role: str, width: int, *, summary_error: bool = False):
    context = browser.new_context(viewport={"width": width, "height": 844 if width < 768 else 900})
    context.add_init_script(f"localStorage.setItem('secretary_token', {json.dumps(_fake_role_token(role))});")
    context.route("**/api/**", lambda route: _respond(route, summary_error=summary_error, role=role))
    context.route("**/auth/me", lambda route: _respond(route, summary_error=summary_error, role=role))
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
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    print("PASS: synthetic-only briefing, rep restriction and summary outage browser checks")


if __name__ == "__main__":
    main()
