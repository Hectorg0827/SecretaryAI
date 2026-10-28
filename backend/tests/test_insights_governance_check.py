"""Governance check preview: §21 rules over user-supplied input, nothing enforced."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import insights
from app.auth.rbac import get_current_user
from app.intelligence.wholesale_distribution.governance import DataLevel, classify_field

URL = "/api/insights/governance-check/preview"


def _client(role="owner", company_id="tenant-a"):
    app = FastAPI()
    app.include_router(insights.router, prefix="/api/insights")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "user-a", "company_id": company_id, "role": role,
    }
    return TestClient(app)


def _body(**overrides):
    body = {
        "role": "analyst",
        "action": "export",
        "fields": ["customer_email", "product_name", "api_key"],
        "data_types": ["orders", "customer_pii"],
        "activity": {"exports_last_hour": 8, "access_hour": 2, "failed_logins_last_5_min": 3},
    }
    body.update(overrides)
    return body


def test_analyst_export_is_checked_field_by_field():
    response = _client().post(URL, json=_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "blocked"
    assert body["blocked_fields"] == 2
    assert body["highest_data_level"] == "restricted"
    fields = {f["field"]: f for f in body["fields"]}
    assert fields["product_name"]["allowed"] is True
    assert fields["product_name"]["data_level"] == "public"
    assert fields["customer_email"]["allowed"] is False
    assert fields["customer_email"]["data_level"] == "confidential"
    assert fields["api_key"]["data_level"] == "restricted"
    assert "ANOMALY LOGGED" not in fields["api_key"]["reason"]
    assert body["role_authority"] == {"view": "confidential", "draft": "confidential", "export": "internal"}

    anomalies = {a["anomaly_type"]: a for a in body["anomalies"]}
    assert set(anomalies) == {"role_escalation_attempt", "bulk_export", "off_hours_access"}
    assert anomalies["bulk_export"]["severity"] == "critical"
    assert anomalies["bulk_export"]["would_block"] is True
    assert anomalies["off_hours_access"]["severity"] == "high"
    assert all("user_id" not in a and "proposed-user" not in a["description"] for a in body["anomalies"])

    retention = {r["data_type"]: r for r in body["retention"]}
    assert retention["orders"]["retention_years"] == 7
    assert retention["customer_pii"]["auto_purge"] is True


def test_permitted_access_has_no_anomalies():
    body = _client().post(URL, json={
        "role": "manager", "action": "approve", "fields": ["gross_margin", "sku"],
        "activity": {"access_hour": 10, "records_modified_last_minute": 4},
    }).json()
    assert body["decision"] == "allowed"
    assert body["blocked_fields"] == 0
    assert body["anomalies"] == []
    assert body["retention"] == []


def test_response_is_advisory_user_supplied_and_tenant_from_token():
    body = _client(company_id="tenant-z").post(URL, json=_body()).json()
    assert body["company_id"] == "tenant-z"
    assert body["status"] == "analysis_only"
    assert body["source"] == "user_supplied"
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False
    assert body["permissions_changed"] is False
    assert body["stored"] is False


@pytest.mark.parametrize("bad", [
    {"role": "owner"},                                   # not a governance role
    {"action": "approve_all"},
    {"fields": []},
    {"fields": [f"field_{i}" for i in range(51)]},
    {"fields": ["sku", "SKU"]},                          # duplicate
    {"fields": ["x"]},                                   # too short
    {"fields": ["drop table; --"]},                      # characters outside the pattern
    {"data_types": ["everything"]},
    {"activity": {"access_hour": 24}},
    {"activity": {"exports_last_hour": -1}},
    {"company_id": "tenant-b"},                          # tenant is never taken from the body
])
def test_invalid_input_is_422(bad):
    assert _client().post(URL, json=_body(**bad)).status_code == 422


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_approve_actions_are_refused(role):
    assert _client(role=role).post(URL, json=_body()).status_code == 403


def test_manager_is_admitted():
    assert _client(role="manager").post(URL, json=_body()).status_code == 200


@pytest.mark.parametrize("name,level", [
    ("price", DataLevel.CONFIDENTIAL),       # contract_price beats public_price
    ("name", DataLevel.CONFIDENTIAL),        # customer_name beats product_name
    ("customer_name_public_price", DataLevel.CONFIDENTIAL),
    ("product_name", DataLevel.PUBLIC),
    ("customer_email", DataLevel.CONFIDENTIAL),
    ("some_random_field_xyz", DataLevel.INTERNAL),
])
def test_partial_field_match_never_downgrades_sensitivity(name, level):
    assert classify_field(name) == level
