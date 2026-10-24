#!/usr/bin/env python3
"""Fail-closed local WCAG 2.2 A/AA audit of the BUILT SecretaryAI frontend.

Run in CI against localhost:4173 Vite preview. All user data is synthetic,
all backend requests are intercepted, and the axe bundle is installed into an
isolated temporary folder, pinned and provided as a path. No production URLs
or credentials are accepted. This is automation evidence, not WCAG certification
or a replacement for human/assistive-technology acceptance.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

WCAG_TAGS = [
    "wcag2a", "wcag2aa", "wcag21a", "wcag21aa",
    "wcag22aa",
]


def _check_local_url(base: str) -> str:
    p = urlparse(base)
    if (p.scheme != "http" or p.hostname not in ("localhost", "127.0.0.1")
            or p.port != 4173 or p.username or p.password or p.query
            or p.fragment or p.path not in ("", "/")):
        raise ValueError("WCAG smoke requires exact local 127.0.0.1:4173 preview")
    return base.rstrip("/")


def _ui_only_owner_token() -> str:
    """Unsigned fake client-side role; never accepted as a real backend token."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"role": "owner", "sub": "synthetic-user"}).encode()
    ).decode().rstrip("=")
    return "head." + payload + ".not-a-signature"


def _mock_backend(context) -> None:
    def reply(route):
        # No outbound user services: synthetic GET success with empty payload.
        route.fulfill(status=200, content_type="application/json", body="{}")
    context.route(re.compile(r"/api/|/auth/me(?:[/?]|$)"), reply)


def _audit_page(page, label: str, axe_path: Path, report_dir: Path,
                failures: list[dict]) -> None:
    page.add_script_tag(path=str(axe_path))
    results = page.evaluate(
        """async (tags) => await window.axe.run(document, {
            runOnly: { type: 'tag', values: tags },
            resultTypes: ['violations'],
        })""",
        WCAG_TAGS,
    )
    findings = []
    for item in results["violations"]:
        findings.append({
            "id": item["id"],
            "impact": item.get("impact"),
            "description": item.get("help"),
            "help_url": item.get("helpUrl"),
            "nodes": [
                {"target": node["target"], "summary": node.get("failureSummary", "")}
                for node in item["nodes"][:15]
            ],
            "nodes_count": len(item["nodes"]),
        })
    report = {
        "page": label,
        "url_path": urlparse(page.url).path,
        "auditor": "axe-core", "wcag_tags": WCAG_TAGS,
        "passes_count": len(results.get("passes", [])),
        "incomplete_count": len(results.get("incomplete", [])),
        "violations": findings,
    }
    (report_dir / (label + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    print(f"WCAG {label}: {len(findings)} violated rules", flush=True)
    if findings:
        failures.extend({"page": label, **issue} for issue in findings)


def run_audit(preview: str, axe_script: Path, report_dir: Path) -> int:
    if not axe_script.is_file():
        raise FileNotFoundError("Pinned local axe-core script is missing")
    report_dir.mkdir(parents=True, exist_ok=True)
    failures: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for width, height in ((390, 844), (1440, 900)):
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                page = context.new_page()
                try:
                    for route, heading, key in (
                        ("/login", "Welcome back", "login"),
                        ("/register", "Create your account", "register"),
                    ):
                        page.goto(preview + route, wait_until="networkidle")
                        assert page.get_by_role("heading", name=heading).is_visible()
                        _audit_page(page, f"{key}-{width}", axe_script, report_dir, failures)

                    # Fresh context: synthetic owner role is only for client-side
                    # navigation. Real authorization and tenant isolation are
                    # exclusively validated in backend and staging suites.
                    context.close()
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                    )
                    context.add_init_script(
                        "localStorage.setItem('secretary_token', "
                        + json.dumps(_ui_only_owner_token()) + ");"
                    )
                    _mock_backend(context)
                    page = context.new_page()
                    page.goto(preview + "/logistics", wait_until="networkidle")
                    assert page.get_by_role("heading", name="Logistics Manager").is_visible()
                    _audit_page(page, f"logistics-{width}", axe_script, report_dir, failures)
                    if width < 768:
                        page.get_by_role("button", name="Open navigation").click()
                        assert page.get_by_role("button", name="Close navigation", exact=True).is_visible()
                        _audit_page(page, f"mobile-menu-{width}", axe_script, report_dir, failures)
                finally:
                    context.close()
        finally:
            browser.close()

    (report_dir / "summary.json").write_text(json.dumps({
        "pages": ["login/register/logistics at 390 and 1440", "mobile menu at 390"],
        "violation_rules": len(failures),
        "failed": bool(failures),
    }, indent=2) + "\n")

    if failures:
        for issue in failures:
            print(f"FAIL WCAG {issue['page']}: {issue['id']} "
                  f"({issue['impact']}) {issue['nodes_count']} nodes", flush=True)
            # A synthetic-only browser is safe to report selector targets.
            # This makes subsequent changes evidence-based, rather than
            # replacing CSS classes without knowing the violating nodes.
            for node in issue["nodes"]:
                print(f"  selector={node['target']} "
                      f"reason={node['summary'][:220]}", flush=True)
        raise AssertionError(
            f"Automated WCAG A/AA violations: {len(failures)} rule/page failures; "
            "review saved JSON reports, no exemptions configured"
        )
    print("PASS: axe-core WCAG 2.2 A/AA selected pages, both viewport sizes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4173")
    parser.add_argument("--axe-script", required=True)
    parser.add_argument("--reports", default="frontend/browser-artifacts/accessibility")
    args = parser.parse_args()
    return run_audit(
        _check_local_url(args.base_url),
        Path(args.axe_script).resolve(),
        Path(args.reports),
    )


if __name__ == "__main__":
    raise SystemExit(main())
