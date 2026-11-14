"""Private-session, GET-only Pricing & Billbacks staging acceptance.

Only the operator's local shell may hold the bearer tokens below. Never paste
them in ChatGPT, CI, GitHub comments, command arguments, or evidence artifacts.
No real price or accounting writes. This script requires three separate
authorized staging users and a real same-tenant purchase assessment.
"""
from __future__ import annotations

import json
import os
import sys
from uuid import UUID

import httpx

from scripts.phase1_authenticated_acceptance import (
    AcceptanceBlocked, check, read_json,
)

BASE = "https://secretaryai-backend-staging.up.railway.app"
BOOK_PATH = "/api/insights/price-book?limit=100"
ASSESSMENTS = "/api/insights/price-book/sources/assessments/"
SUGGESTIONS = "/api/insights/pricing-suggestions"


def session(name: str) -> httpx.Client:
    secret = os.environ.get(name, "").strip()
    check(bool(secret), "Required private staging session is missing: " + name)
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": "Bearer " + secret, "Accept": "application/json"},
        timeout=httpx.Timeout(45, connect=15), follow_redirects=False,
    )


def confirm_role(client: httpx.Client, roles: set[str]) -> str:
    profile = read_json(client, "/auth/me")
    role = profile.get("role")
    check(role in roles, "Staging session has an unexpected current role")
    return role


def verify_book(client: httpx.Client) -> dict:
    book = read_json(client, BOOK_PATH)
    check(book.get("status") == "analysis_only"
          and book.get("source") == "connected_data"
          and book.get("requires_human_review") is True
          and book.get("actions_executed") is False
          and book.get("price_changes_allowed") is False
          and book.get("pricing_approved_by_this_endpoint") is False
          and book.get("cost_source_certified") is False,
          "Price book implies approval, price writes, or certified costs")
    company = book.get("company_id")
    check(isinstance(company, str) and bool(company),
          "Price book did not identify the authenticated company")
    items = book.get("items")
    check(isinstance(items, list) and all(isinstance(i, dict) for i in items),
          "Price book rows malformed")
    check(book.get("count_returned") == len(items),
          "Price book count does not match returned entries")
    check(book.get("book_state") ==
          ("unpopulated" if not items else "records_present"),
          "Price book record state inconsistent")
    return {"company": company, "count": len(items),
            "book_state": book["book_state"]}


def verify_assessment(client: httpx.Client, assessment_id: str) -> dict:
    try:
        safe_id = str(UUID(assessment_id))
    except (ValueError, AttributeError) as exc:
        raise AcceptanceBlocked("Real staging assessment UUID is invalid") from exc
    source = read_json(client, ASSESSMENTS + safe_id)
    check(source.get("status") == "analysis_only"
          and source.get("actions_executed") is False
          and source.get("valuation_verified") is False
          and source.get("accounting_reconciled") is False
          and source.get("preallocated_landed_cost_not_applied") is True
          and source.get("read_consistency") ==
          "separate_tenant_scoped_queries_not_atomic_snapshot",
          "Purchasing preview falsely certifies finance or duplicates charges")
    check(source.get("assessment_id") == safe_id,
          "Assessment identity differs from requested source")
    check(source.get("assertion_status") in ("provisional", "reconciled"),
          "Assessment status is unrecognized")
    check(isinstance(source.get("lines"), list) and bool(source["lines"]),
          "Assessment has no verifiable line structure")
    return {"assertion_status": source["assertion_status"],
            "line_count": len(source["lines"]),
            "accounting_reconciled": False}


def verify_suggestions(client: httpx.Client, company: str) -> dict:
    data = read_json(client, SUGGESTIONS)
    check(data.get("company_id") == company and
          data.get("status") == "analysis_only" and
          data.get("source") == "connected_data" and
          data.get("actions_executed") is False and
          data.get("prices_changed") is False,
          "Suggestion source is outside tenant or implies a price write")
    rows = data.get("items")
    check(isinstance(rows, list), "Suggestions did not return a list")
    return {"count": len(rows), "prices_changed": False}


def verify_denied(client: httpx.Client) -> dict:
    confirm_role(client, {
        "back_office", "sales_rep", "viewer",
        "finance_controller", "ar_specialist", "ap_specialist",
    })
    result = client.get(BOOK_PATH)
    check(result.status_code == 403,
          "Unprivileged staff did not receive 403 on price-book access")
    return {"book_access": "DENIED_403"}


def check_role_matrix(owner: httpx.Client, pricing: httpx.Client,
                      denied: httpx.Client, assessment_id: str) -> dict:
    confirm_role(owner, {"owner", "manager"})
    confirm_role(pricing, {"pricing_billbacks"})
    a = verify_book(owner)
    b = verify_book(pricing)
    check(a["company"] == b["company"],
          "Owner and pricing staff belong to different companies")
    check(a["count"] == b["count"] and a["book_state"] == b["book_state"],
          "Owner and pricing staff see incompatible price-book sources")
    owner_assessment = verify_assessment(owner, assessment_id)
    staff_assessment = verify_assessment(pricing, assessment_id)
    check(owner_assessment == staff_assessment,
          "Owner and pricing staff see inconsistent purchase assessment evidence")
    rejection = verify_denied(denied)
    suggestions = verify_suggestions(owner, a["company"])
    return {
        "pricing_book": {"result": "SOURCE_PARITY_PASS",
                         "records": a["count"], "state": a["book_state"]},
        "assessment": owner_assessment,
        "pricing_role": "ALLOWED",
        "denied_role": rejection,
        "suggestions": suggestions,
        "accounting_certification": "NOT_ESTABLISHED",
        "price_approval_or_publication": "NOT_PERFORMED",
        "release_signoff": "MANUAL_EVIDENCE_REVIEW_REQUIRED",
    }


def main() -> int:
    result = {"environment": "staging", "read_only": True,
              "authentication": "operator private sessions", "result": "BLOCKED"}
    try:
        assessment_id = os.environ.get("LIONBI_STAGING_ASSESSMENT_ID", "").strip()
        check(bool(assessment_id), "A real approved-scope assessment ID is required")
        with session("LIONBI_STAGING_BEARER_TOKEN") as owner, \
             session("LIONBI_STAGING_PRICING_BEARER_TOKEN") as pricing, \
             session("LIONBI_STAGING_DENIED_BEARER_TOKEN") as denied:
            result.update(check_role_matrix(owner, pricing, denied, assessment_id))
            result["result"] = "READ_ONLY_ROLE_AND_SOURCE_PARITY_PASS"
            # No automated independent accounting or actual external provider proof.
            print(json.dumps(result, sort_keys=True))
            return 2
    except (AcceptanceBlocked, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        result["reason_type"] = type(exc).__name__
        result["reason"] = (str(exc) if isinstance(exc, AcceptanceBlocked)
                            else "Staging source or transport was unavailable")
        print(json.dumps(result, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
