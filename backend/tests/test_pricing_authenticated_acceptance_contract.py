"""No-network contract tests for the private-session pricing acceptance probe."""
import json

import pytest

from scripts.pricing_authenticated_acceptance import (
    ASSESSMENTS, BOOK_PATH, SUGGESTIONS, AcceptanceBlocked,
    check_role_matrix, verify_assessment, verify_book, verify_foreign_tenant,
)


ASSESSMENT = "123e4567-e89b-12d3-a456-426614174001"


class Response:
    def __init__(self, code, payload=None):
        self.status_code = code
        self.payload = payload or {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self.payload


class Client:
    def __init__(self, role, company="tenant-a", book=None, assessment=None,
                 suggestions=None, foreign_assessment_status=None):
        self.role = role
        self.company = company
        self.book = book
        self.assessment = assessment
        self.suggestions = suggestions
        self.foreign_assessment_status = foreign_assessment_status
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path == "/auth/me":
            return Response(200, {"role": self.role})
        if path == BOOK_PATH:
            if self.role not in {"owner", "manager", "pricing_billbacks"}:
                return Response(403)
            return Response(200, self.book or book_payload(self.company))
        if path == ASSESSMENTS + ASSESSMENT:
            return Response(
                self.foreign_assessment_status, {}
            ) if self.foreign_assessment_status is not None else Response(
                200, self.assessment or assessment_payload())
        if path == SUGGESTIONS:
            return Response(200, self.suggestions or suggestions_payload(self.company))
        return Response(404)


def book_payload(company):
    return {
        "status": "analysis_only", "source": "connected_data",
        "requires_human_review": True, "actions_executed": False,
        "price_changes_allowed": False,
        "pricing_approved_by_this_endpoint": False,
        "cost_source_certified": False, "company_id": company,
        "items": [], "count_returned": 0, "book_state": "unpopulated",
    }


def assessment_payload():
    return {
        "status": "analysis_only", "actions_executed": False,
        "assessment_id": ASSESSMENT, "valuation_verified": False,
        "accounting_reconciled": False,
        "preallocated_landed_cost_not_applied": True,
        "read_consistency": "separate_tenant_scoped_queries_not_atomic_snapshot",
        "assertion_status": "provisional", "lines": [{"sku": "test"}],
    }


def suggestions_payload(company):
    return {
        "company_id": company, "status": "analysis_only",
        "source": "connected_data", "actions_executed": False,
        "prices_changed": False, "items": [],
    }


def test_role_matrix_requires_three_real_permission_classes():
    owner = Client("owner")
    pricing = Client("pricing_billbacks")
    denied = Client("back_office")
    result = check_role_matrix(owner, pricing, denied, ASSESSMENT)
    assert result["pricing_book"]["state"] == "unpopulated"
    assert result["denied_role"]["book_access"] == "DENIED_403"
    assert result["accounting_certification"] == "NOT_ESTABLISHED"
    assert result["release_signoff"] == "MANUAL_EVIDENCE_REVIEW_REQUIRED"
    assert all(x in owner.paths for x in (BOOK_PATH, SUGGESTIONS, ASSESSMENTS+ASSESSMENT))


def test_wrong_tenant_pricing_staff_is_not_accepted():
    with pytest.raises(AcceptanceBlocked, match="different companies"):
        check_role_matrix(Client("owner", company="a"),
                          Client("pricing_billbacks", company="b"),
                          Client("back_office"), ASSESSMENT)


def test_wrong_staff_role_cannot_impersonate_pricing_staff():
    with pytest.raises(AcceptanceBlocked, match="unexpected current role"):
        check_role_matrix(Client("owner"), Client("back_office"),
                          Client("viewer"), ASSESSMENT)


def test_book_never_claims_approved_price():
    raw = book_payload("tenant-a")
    raw["cost_source_certified"] = True
    with pytest.raises(AcceptanceBlocked, match="falsely|implies"):
        verify_book(Client("owner", book=raw))


def test_assessment_must_not_claim_accounting_is_reconciled():
    raw = assessment_payload()
    raw["accounting_reconciled"] = True
    with pytest.raises(AcceptanceBlocked, match="falsely"):
        verify_assessment(Client("owner", assessment=raw), ASSESSMENT)


def test_source_absence_is_not_approved_or_recorded_as_zero():
    obj = Client("owner")
    obj.book = {"items": [], "count_returned": 0}
    with pytest.raises(AcceptanceBlocked):
        verify_book(obj)

def test_cross_tenant_assessment_is_not_readable():
    owner = Client("owner")
    pricing = Client("pricing_billbacks")
    denied = Client("back_office")
    foreign = Client("manager", company="tenant-b",
                     foreign_assessment_status=404)
    result = check_role_matrix(owner, pricing, denied, ASSESSMENT, foreign)
    assert result["cross_tenant"] == {
        "assessment_access": "DENIED_404", "distinct_tenant": True,
    }
    assert ASSESSMENTS + ASSESSMENT in foreign.paths


@pytest.mark.parametrize("code", [200, 401, 403, 500, 503])
def test_cross_tenant_fails_closed_for_non_404_responses(code):
    foreign = Client("manager", company="tenant-b",
                     foreign_assessment_status=code)
    with pytest.raises(AcceptanceBlocked, match="did not return 404"):
        verify_foreign_tenant(foreign, "tenant-a", ASSESSMENT)


def test_foreign_tenant_rejected_if_session_same_company():
    foreign = Client("manager", company="tenant-a",
                     foreign_assessment_status=404)
    with pytest.raises(AcceptanceBlocked, match="belongs to the owner tenant"):
        verify_foreign_tenant(foreign, "tenant-a", ASSESSMENT)


def test_optional_cross_tenant_must_be_explicitly_unverified_when_absent():
    result = check_role_matrix(
        Client("owner"), Client("pricing_billbacks"),
        Client("back_office"), ASSESSMENT,
    )
    assert result["cross_tenant"]["assessment_access"] == (
        "NOT_RUN_SECOND_TENANT_SESSION_REQUIRED"
    )
