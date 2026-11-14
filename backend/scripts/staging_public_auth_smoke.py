"""Public staging smoke checks: live health + fail-closed authorization.

This script performs only credential-free GET requests to the allowlisted
staging backend. It never logs response bodies, sends user tokens, creates
records, changes prices, or qualifies as authenticated business acceptance.
Exit nonzero on a network outage, redirect, unexpected 404 or protected 200.
"""
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ORIGIN = "https://secretaryai-backend-staging.up.railway.app"
PROTECTED = (
    "/auth/me",
    "/api/insights/price-book",
    "/api/insights/price-book/sources/assessments/00000000-0000-0000-0000-000000000000",
    "/api/insights/pricing-suggestions",
    "/api/logistics/journey/shipments",
    "/api/logistics/journey/purchasing-coverage",
    "/api/logistics/monitor",
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch_status(path: str) -> int:
    if not path.startswith("/") or "://" in path or ".." in path:
        raise ValueError("Only fixed relative paths allowed")
    request = Request(ORIGIN + path, headers={"Accept": "application/json"}, method="GET")
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def verify(fetch=fetch_status) -> dict:
    outcome = {
        "environment": "staging",
        "read_only": True,
        "authentication": "no credentials sent",
        "business_acceptance": False,
        "checks": [],
    }
    health = fetch("/health")
    outcome["checks"].append({"endpoint": "health", "actual": health, "expected": 200,
                               "passed": health == 200})
    for path in PROTECTED:
        actual = fetch(path)
        outcome["checks"].append({"endpoint": path, "actual": actual,
                                   "expected": 401, "passed": actual == 401})
    outcome["result"] = "PASS_PUBLIC_AUTH_ONLY" if all(
        item["passed"] for item in outcome["checks"]
    ) else "FAILED_PUBLIC_AUTH_BOUNDARY"
    return outcome


def main() -> int:
    try:
        result = verify()
    except (OSError, URLError, ValueError) as exc:
        # Exception messages may include proxy, URL or infrastructure details.
        result = {"environment": "staging", "result": "BLOCKED_TRANSPORT",
                  "reason_type": type(exc).__name__, "business_acceptance": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["result"] == "PASS_PUBLIC_AUTH_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
