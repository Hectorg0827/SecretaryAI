"""Private-session, GET-only Pricing & Billbacks staging acceptance.

Only the operator's local shell may hold the bearer tokens below. Never paste
them in ChatGPT, CI, GitHub comments, command arguments, or evidence artifacts.
The primary session must be an active company OWNER, because tenant membership
of the restricted staff is independently verified with /api/team/members.
No real price or accounting writes. This script requires three separate
authorized staging users and a real same-tenant purchase assessment. A fourth
private owner/manager token from another tenant optionally verifies 404 cross-
tenant denial without manufacturing another user's account or data.
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


def verify_denied(owner: httpx.Client, client: httpx.Client) -> dict:
    """Do not mistake foreign-company isolation for same-company role denial.

    The test must use a real, active staff member from the owner's company.
    /api/team/members is deliberately owner-only and supplies the independent
    company membership evidence; never print member details or identifiers.
    """
    current = read_json(client, "/auth/me")
    role = current.get("role")
    check(role in {"back_office", "sales_rep", "viewer",
                   "finance_controller", "ar_specialist", "ap_specialist"},
          "Denied-user session has an unexpected current role")
    user_id = current.get("id")
    check(isinstance(user_id, str) and bool(user_id),
          "Denied-user session lacks a stable identity")
    members = read_json(owner, "/api/team/members").get("members")
    check(isinstance(members, list),
          "Owner's tenant membership source unavailable")
    matching = [m for m in members if isinstance(m, dict)
                and m.get("id") == user_id]
    check(len(matching) == 1
          and matching[0].get("is_active") is True
          and matching[0].get("role") == role,
          "Denied user is not an active restricted member of owner's tenant")
    result = client.get(BOOK_PATH)
    check(result.status_code == 403,
          "Same-tenant restricted staff did not receive pricing HTTP 403")
    return {"book_access": "DENIED_403", "same_tenant_active_role": True}


def verify_foreign_tenant(foreign: httpx.Client, owner_company: str,
                          assessment_id: str) -> dict:
    """Use a separate current owner/manager session, with no tenant spoofing.

    The authorized owner's request above must have already established the
    assessment exists. A valid independent tenant must see a 404, not the
    original tenant's financial details, and not a provider-error 503.
    """
    confirm_role(foreign, {"owner", "manager"})
    other_book = verify_book(foreign)
    check(other_book["company"] != owner_company,
          "Foreign-tenant test session belongs to the owner tenant")
    safe_id = str(UUID(assessment_id))
    response = foreign.get(ASSESSMENTS + safe_id)
    check(response.status_code == 404,
          "Foreign-tenant assessment request did not return 404")
    return {"assessment_access": "DENIED_404", "distinct_tenant": True}


def check_role_matrix(owner: httpx.Client, pricing: httpx.Client,
                      denied: httpx.Client, assessment_id: str,
                      foreign: httpx.Client | None = None) -> dict:
    confirm_role(owner, {"owner"})
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
    rejection = verify_denied(owner, denied)
    suggestions = verify_suggestions(owner, a["company"])
    isolation = (verify_foreign_tenant(foreign, a["company"], assessment_id)
                 if foreign is not None else
                 {"assessment_access": "NOT_RUN_SECOND_TENANT_SESSION_REQUIRED"})
    return {
        "pricing_book": {"result": "SOURCE_PARITY_PASS",
                         "records": a["count"], "state": a["book_state"]},
        "assessment": owner_assessment,
        "pricing_role": "ALLOWED",
        "denied_role": rejection,
        "cross_tenant": isolation,
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
        from contextlib import ExitStack
        with ExitStack() as stack:
            owner = stack.enter_context(session("LIONBI_STAGING_BEARER_TOKEN"))
            pricing = stack.enter_context(
                session("LIONBI_STAGING_PRICING_BEARER_TOKEN"))
            denied = stack.enter_context(
                session("LIONBI_STAGING_DENIED_BEARER_TOKEN"))
            foreign = None
            if os.environ.get("LIONBI_STAGING_FOREIGN_BEARER_TOKEN", "").strip():
                foreign = stack.enter_context(
                    session("LIONBI_STAGING_FOREIGN_BEARER_TOKEN"))
            result.update(check_role_matrix(owner, pricing, denied, assessment_id,
                                            foreign=foreign))
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
