#!/usr/bin/env python3
"""Fail Gate A on unintegrated Morning Briefing web/mobile wire contracts.

Static integration sentinel, not an E2E behavioral or deployed UX test.
It runs only after all five workstream branches are combined in temporary CI.
The backend response contract is authoritative; do not substitute client-side
aggregation for its six evidence-scoped sections.

No network/credentials, writes, test fixtures, app startup or provider effects.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_API = ROOT / "frontend/src/lib/api.ts"
BRIEFING = ROOT / "frontend/src/components/dashboard/MorningBriefing.tsx"
MOBILE_API = ROOT / "mobile/src/api/dashboard.ts"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def check_contract(
    frontend_api: str, briefing_ui: str, mobile_api: str,
) -> list[str]:
    problems: list[str] = []

    # Require the canonical route to be typed and named in the web API layer.
    # The path alone, embedded as an unused constant, is insufficient.
    if "/api/briefing/today" not in frontend_api:
        problems.append("Web API client must request /api/briefing/today")
    if not re.search(r"\bbriefing\s*:\s*\{[\s\S]*?\btoday\s*:", frontend_api):
        problems.append("Web API client must expose api.briefing.today()")
    if not re.search(r"\bBriefingToday(?:Response(?:V1)?|V1)\b", frontend_api):
        problems.append("Web API must publish a typed BriefingToday v1 response")

    if not re.search(r"\bapi\.briefing\.today\s*\(", briefing_ui):
        problems.append("MorningBriefing must consume api.briefing.today(), not synthesize its own digest")
    for field in ("complete_digest", "source_quality", "source_coverage"):
        if field not in briefing_ui:
            problems.append(f"MorningBriefing must display/handle canonical {field} status")
    if "Source unavailable" not in briefing_ui and "unavailable" not in briefing_ui.lower():
        problems.append("MorningBriefing must explicitly render unavailable source state")

    # These six tiles are canonical, not an aggregate inferred from separate
    # browser reads of sales, feed, pending drafts and tenant audit records.
    if "Promise.allSettled([" in briefing_ui:
        problems.append(
            "MorningBriefing must not construct the canonical six-section digest "
            "from independent browser-side Promise.allSettled reads"
        )

    # The server explicitly returns accounts=null for roles without
    # company-wide account visibility. TypeScript must not assume object-only.
    for name, src in (("Web", frontend_api), ("Mobile", mobile_api)):
        # Restrict the search to DashboardSummary, not unrelated interfaces
        # (e.g. AccountSearchResponse). Allow inline object type or named type,
        # including the equivalent "null | AccountHealth" order.
        interface = re.search(
            r"\b(?:export\s+)?interface\s+DashboardSummary\b[\s\S]*?\{",
            src,
        )
        body = src[interface.end():] if interface else ""
        account_decl = re.search(
            r"\baccounts\s*:\s*"
            r"(?:(?:\{[^{}]*\}|[A-Za-z_$][\w$]*)\s*\|\s*null"
            r"|null\s*\|\s*(?:\{[^{}]*\}|[A-Za-z_$][\w$]*))"
            r"\s*[;,]",
            body,
        )
        if not account_decl:
            problems.append(f"{name} DashboardSummary.accounts must explicitly allow null")

    return problems


def main() -> int:
    failures = check_contract(
        _read(FRONTEND_API), _read(BRIEFING), _read(MOBILE_API)
    )
    if failures:
        print("Gate A frontend/backend contract NOT READY:", file=sys.stderr)
        for issue in failures:
            print(f"  - {issue}", file=sys.stderr)
        print(
            "Update the Chat 5 workstream only, then rerun disposable "
            "five-branch CI. Do not merge or deploy to bypass this gate.",
            file=sys.stderr,
        )
        return 1
    print("Gate A frontend/backend static wire contract checks passed.")
    print("Separate UI behavior, deployed E2E and provider verification still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
