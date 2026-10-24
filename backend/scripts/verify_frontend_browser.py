#!/usr/bin/env python3
"""Cross-engine visual smoke: built SecretaryAI web UI at mobile and desktop sizes.

No staging service calls, no real data, no screenshots of user information.
Test tokens are deliberately NOT signed; backend authentication is not tested.
Real API staging and human visual-diff review remain independent release gates.

Requires: pip install playwright==1.48.0; python -m playwright install chromium firefox webkit
Invoke after starting Vite preview (serving the BUILT frontend dist folder):
  python backend/scripts/verify_frontend_browser.py --base-url http://127.0.0.1:4173
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def _ready(url: str) -> None:
    for _ in range(45):
        try:
            with urlopen(url + "/login", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(1)
    raise AssertionError("The built frontend preview was not reachable")


def _fake_user_role(role: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"role": role, "sub": "test-user"}).encode()
    ).decode().rstrip("=")
    return "dummy." + payload + ".no-signature"


def _measure(page, label: str):
    values = page.evaluate("""() => ({
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
    })""")
    assert values["documentWidth"] <= values["viewportWidth"] + 2, (
        label + " horizontal document overflow: " + str(values)
    )
    assert values["bodyWidth"] <= values["viewportWidth"] + 2, (
        label + " horizontal body overflow: " + str(values)
    )


def _mock_internal_api(context):
    # No actual backend/API/credentials; static UI and CSS only.
    def respond(route):
        route.fulfill(status=200, content_type="application/json", body="{}")
    context.route(re.compile(r"/api/|/auth/me(?:[/?]|$)"), respond)


def _login_and_register(browser, engine: str, base: str, artifacts: Path, width: int, height: int):
    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
    page = context.new_page()
    page.goto(base + "/login", wait_until="networkidle")
    assert page.get_by_role("heading", name="Welcome back").is_visible()
    email = page.get_by_label("Email address")
    password = page.get_by_label("Password")
    assert email.is_visible() and password.is_visible()
    assert page.get_by_role("button", name="Sign in").is_disabled()
    _measure(page, "login " + str(width))
    background = page.locator(".min-h-screen").first.evaluate(
        "(element) => getComputedStyle(element).backgroundColor"
    )
    # Chromium/Firefox/WebKit serialize the same Tailwind OKLCH hue with
    # slightly different decimal precision. Check the numeric components
    # with tight tolerance rather than requiring identical string rendering.
    if background != "rgb(15, 23, 42)":
        match = re.fullmatch(
            r"oklch\((\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\)",
            background,
        )
        assert match is not None and all(
            abs(float(actual) - expected) < 0.001
            for actual, expected in zip(match.groups(), (0.208, 0.042, 265.755))
        ), "Tailwind slate login background missing: " + background
    page.screenshot(path=str(artifacts / (engine + "-login-" + str(width) + ".png")), full_page=True)
    page.goto(base + "/register", wait_until="networkidle")
    assert page.get_by_role("heading", name="Create your account").is_visible()
    assert page.get_by_label("Company name").is_visible()
    _measure(page, "register " + str(width))
    page.screenshot(path=str(artifacts / (engine + "-register-" + str(width) + ".png")), full_page=True)
    page.goto(base + "/logistics", wait_until="networkidle")
    assert page.get_by_role("heading", name="Welcome back").is_visible(), (
        "Unauthenticated protected navigation did not redirect to login"
    )
    context.close()


def _authenticated_navigation(browser, engine: str, base: str, artifacts: Path, width: int, height: int):
    mobile = width < 768
    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
    context.add_init_script(
        "localStorage.setItem('secretary_token', " + json.dumps(_fake_user_role("owner")) + ");"
    )
    _mock_internal_api(context)
    page = context.new_page()
    page.goto(base + "/logistics", wait_until="networkidle")
    assert page.get_by_role("heading", name="Logistics Manager").is_visible()
    aside = page.locator("#secretaryai-sidebar")
    assert aside.count() == 1
    if mobile:
        assert aside.evaluate("(element) => element.inert") is True, (
            "Offscreen mobile links are keyboard reachable before opening"
        )
        open_btn = page.get_by_role("button", name="Open navigation")
        assert open_btn.is_visible()
        aside_box = aside.bounding_box()
        assert aside_box is not None and aside_box["x"] < 0, (
            "Mobile nav unexpectedly covers page on first load"
        )
        open_btn.click()
        assert open_btn.get_attribute("aria-expanded") == "true"
        assert aside.evaluate("(element) => element.inert") is False
        assert aside.get_attribute("role") == "dialog"
        assert aside.get_attribute("aria-modal") == "true"
        close_button = page.get_by_role("button", name="Close navigation", exact=True)
        assert close_button.evaluate("(element) => document.activeElement === element"), "Drawer must focus close control on open"
        page.keyboard.press("Shift+Tab")
        assert page.get_by_role("button", name="Sign out").evaluate("(element) => document.activeElement === element"), (
            "Shift+Tab leaked out of navigation modal"
        )
        page.keyboard.press("Tab")
        assert close_button.evaluate("(element) => document.activeElement === element"), "Tab did not wrap to first control"
        page.wait_for_function("document.querySelector('#secretaryai-sidebar').getBoundingClientRect().left >= -1")
        page.screenshot(path=str(artifacts / (engine + "-navigation-open-" + str(width) + ".png")))
        page.get_by_role("link", name="Logistics").click()
        assert open_btn.get_attribute("aria-expanded") == "false"
        assert open_btn.evaluate("(element) => document.activeElement === element"), "Route dismissal did not restore opener focus"
        assert aside.evaluate("(element) => element.inert") is True
        page.wait_for_function("document.querySelector('#secretaryai-sidebar').getBoundingClientRect().right <= 1")
        open_btn.click()
        page.keyboard.press("Escape")
        assert open_btn.get_attribute("aria-expanded") == "false", "Escape did not close mobile navigation"
        assert open_btn.evaluate("(element) => document.activeElement === element"), "Escape dismissal did not restore opener focus"
        assert aside.evaluate("(element) => element.inert") is True
    else:
        assert page.get_by_role("button", name="Open navigation").is_hidden()
        assert aside.evaluate("(element) => element.inert") is False, (
            "Desktop sidebar must not remain inert"
        )
        aside_box = aside.bounding_box()
        assert aside_box is not None and 0 <= aside_box["x"] <= 1
        assert aside_box["width"] >= 200

    page.get_by_role("button", name="Purchase Orders").click()
    # Correct tab switch is verified through the changed active button styling.
    assert "bg-slate-800" in page.get_by_role("button", name="Purchase Orders").get_attribute("class")
    _measure(page, "logistics " + str(width))
    page.screenshot(path=str(artifacts / (engine + "-logistics-" + str(width) + ".png")))
    context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4173")
    parser.add_argument("--screenshots", default="frontend/browser-artifacts")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if base not in ("http://127.0.0.1:4173", "http://localhost:4173"):
        raise SystemExit("Browser smoke may target only the local built preview server")
    artifacts = Path(args.screenshots)
    artifacts.mkdir(parents=True, exist_ok=True)
    _ready(base)
    with sync_playwright() as playwright:
        for engine in ("chromium", "firefox", "webkit"):
            browser = getattr(playwright, engine).launch(headless=True)
            try:
                for width, height in ((390, 844), (1440, 900)):
                    _login_and_register(browser, engine, base, artifacts, width, height)
                    _authenticated_navigation(browser, engine, base, artifacts, width, height)
                print("PASS: " + engine + " mobile/desktop, keyboard and layout checks")
            finally:
                browser.close()
    print("PASS: local Chromium, Firefox and WebKit browser acceptance (6 viewports), "
          "login/register, modal keyboard focus, protected navigation, CSS and overflow")
    print("Screenshots are evidence for manual comparison, NOT approved visual baselines")


if __name__ == "__main__":
    main()
