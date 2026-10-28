"""HTTP contract regression: supplier text is an unverified analysis, not a PO event."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user


def _client(role="manager"):
    db = MagicMock()
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "employee-1", "company_id": "tenant-1", "role": role,
    }
    app.dependency_overrides[logistics.get_db] = lambda: db
    return TestClient(app), db


def _body(subject="Order confirmation", body="We confirm that we will ship the order"):
    return {
        "po_number": "PO-101", "supplier_id": "supplier-1",
        "email_subject": subject, "email_body": body,
    }


def test_http_vendor_parsing_is_read_only_and_never_claims_verified_confirmation():
    client, db = _client()
    response = client.post("/api/logistics/vendor/parse-response", json=_body())
    assert response.status_code == 200, response.text
    parsed = response.json()
    assert parsed["po_number"] == "PO-101"
    assert parsed["candidate_confirmation"] is True
    assert parsed["status"] == "analysis_only"
    assert parsed["message_verified"] is False
    assert parsed["po_link_verified"] is False
    assert parsed["requires_human_review"] is True
    assert parsed["supplier_action_permitted"] is False
    assert parsed["price_review_required"] is True
    assert parsed["quantity_review_required"] is True
    db.table.assert_not_called()
    db.rpc.assert_not_called()


def test_http_vendor_rejection_and_ambiguity_remain_unverified():
    client, db = _client()
    rejected = client.post("/api/logistics/vendor/parse-response", json=_body(
        subject="PO reply", body="We are unable to process your order."
    ))
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["candidate_rejection"] is True
    assert rejected.json()["status"] == "analysis_only"

    ambiguous = client.post("/api/logistics/vendor/parse-response", json=_body(
        subject="Hello", body="We will get back to you later."
    ))
    assert ambiguous.status_code == 200, ambiguous.text
    assert ambiguous.json()["candidate_confirmation"] is None
    assert ambiguous.json()["requires_human_review"] is True
    db.table.assert_not_called()


def test_http_vendor_missing_content_and_unauthorized_role_fail_closed():
    client, db = _client()
    missing = client.post("/api/logistics/vendor/parse-response", json=_body(body=""))
    assert missing.status_code == 422
    db.table.assert_not_called()

    viewer, viewer_db = _client(role="viewer")
    denied = viewer.post("/api/logistics/vendor/parse-response", json=_body())
    assert denied.status_code == 403
    viewer_db.table.assert_not_called()
