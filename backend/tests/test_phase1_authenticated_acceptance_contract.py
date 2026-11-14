"""Synthetic contract tests for the operator-only live staging acceptance runner."""
import httpx
import pytest

from scripts import phase1_authenticated_acceptance as acceptance


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def get(self, path):
        payload = self.responses.get(path)
        if payload is None:
            return httpx.Response(503, json={"detail": "synthetic source failure"})
        return httpx.Response(200, json=payload)


def test_staging_client_rejects_other_origins_before_using_any_token(monkeypatch):
    monkeypatch.setenv("LIONBI_STAGING_BEARER_TOKEN", "synthetic-do-not-use")
    for base in (
        "https://secretaryai-backend.up.railway.app",
        "http://secretaryai-backend-staging.up.railway.app",
        "https://secretaryai-backend-staging.up.railway.app.attacker.invalid",
        "https://secretaryai-backend-staging.up.railway.app:8443",
        "https://secretaryai-backend-staging.up.railway.app/login",
    ):
        with pytest.raises(acceptance.AcceptanceBlocked):
            acceptance.configured_client(base)


def test_staging_client_never_allows_missing_credentials(monkeypatch):
    monkeypatch.delenv("LIONBI_STAGING_BEARER_TOKEN", raising=False)
    with pytest.raises(acceptance.AcceptanceBlocked):
        acceptance.configured_client("https://secretaryai-backend-staging.up.railway.app")


def test_account_api_parity_uses_invoice_evidence_and_correct_status():
    c = FakeClient({
        "/api/accounts/?limit=200": {"accounts": [
            {"id": "C1", "name": "Synthetic buyer", "health_status": "slowing", "health_score": 55}
        ], "total": 1},
        "/api/accounts/C1": {"id": "C1", "name": "Synthetic buyer",
                             "health_status": "slowing", "health_score": 55,
                             "recent_invoices": [{"id": "I1"}]},
        "/api/accounts/C1/orders?limit=500": {"account_id": "C1",
            "orders": [{"id": "I1"}], "total": 1, "history_window_days": 365},
    })
    status, detail = acceptance.compare_accounts(c)
    assert status["result"] == "PASS"
    assert status["invoice_rows"] == 1
    assert detail["health_status"] == "slowing"


def test_inventory_unknown_velocity_cannot_be_claimed_healthy():
    c = FakeClient({
        "/api/inventory/?limit=200&offset=0": {"items": [
            {"item_id": "SKU1", "product_name": "Synthetic item", "total_qty": 10,
             "stock_status": "unassessed", "weekly_sell_rate_observed": False,
             "weeks_remaining": None},
        ], "total": 1},
        "/api/inventory/alerts": {"alerts": [], "count": 0, "unassessed_count": 1},
    })
    status, item = acceptance.compare_inventory(c)
    assert status["unassessed_count"] == 1
    assert item["stock_status"] == "unassessed"


def test_inventory_zero_qty_is_rejected_when_misclassified():
    c = FakeClient({
        "/api/inventory/?limit=200&offset=0": {"items": [
            {"total_qty": 0, "stock_status": "healthy", "weekly_sell_rate_observed": False}
        ], "total": 1},
    })
    with pytest.raises(acceptance.AcceptanceBlocked):
        acceptance.compare_inventory(c)


def test_compliance_registry_must_match_setup_counts_across_six_tables():
    counts = {
        "products_count": 0, "licenses_count": 0, "federal_permits_count": 0,
        "distributors_count": 0, "brand_registrations_count": 0, "colas_count": 0,
    }
    responses = {
        "/api/compliance/setup-status": {"is_setup": False, "missing": ["licenses"], **counts},
        "/api/compliance/status": {"overall": "ok"},
        "/api/compliance/alerts": {"alerts": [], "count": 0},
    }
    for slug in ("products", "licenses", "federal-permits", "distributors",
                 "brand-registrations", "colas"):
        responses["/api/compliance/" + slug] = {"count": 0}
    status, setup = acceptance.compare_compliance(FakeClient(responses))
    assert status["overall"] == "unverified"
    assert status["result"] == "INCOMPLETE_REGISTRY"
    assert setup["is_setup"] is False

    responses["/api/compliance/colas"] = {"count": 1}
    with pytest.raises(acceptance.AcceptanceBlocked):
        acceptance.compare_compliance(FakeClient(responses))
