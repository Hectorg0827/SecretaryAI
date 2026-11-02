"""Supplier TCO insight: a what-if on entered supplier costs and ESG figures."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.insights import supplier_tco
from app.auth.rbac import get_current_user


def client(role="owner"):
    app = FastAPI()
    app.include_router(supplier_tco.router, prefix="/api/insights" + supplier_tco.PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "tenant-a", "sub": "user-a", "role": role,
    }
    return TestClient(app)


def supplier(name, price, **overrides):
    row = {
        "name": name, "purchase_price": price, "inbound_freight_per_unit": 1.0,
        "customs_duty_rate_pct": 5, "brokerage_per_unit": 0.2, "days_inventory_outstanding": 60,
        "holding_cost_rate_pct": 25, "return_rate_pct": 2, "return_handling_cost": 5,
        "replacement_cost_per_return": 10, "kg_co2e_per_unit": 2, "volume_rebate_pct": 0,
    }
    row.update(overrides)
    return row


def body(**overrides):
    payload = {
        "product": "Malta 24x12oz", "annual_volume": 10_000, "unit_of_measure": "case",
        "currency": "USD", "current_supplier": "Andes Bottling",
        "suppliers": [
            supplier("Andes Bottling", 10, esg={
                "carbon_per_case": 1.0, "packaging_waste_kg_per_unit": 0.3,
                "recyclable_packaging_pct": 60, "water_intensity": 2, "safety_incidents": 0,
                "supplier_audit_pass_rate": 90, "local_sourcing_pct": 40,
                "on_time_payment_rate": 95, "import_compliance_rate": 97,
            }),
            supplier("Caribe Foods", 8.5, inbound_freight_per_unit=1.5, customs_duty_rate_pct=10,
                     brokerage_per_unit=0.3, days_inventory_outstanding=90, return_rate_pct=1,
                     kg_co2e_per_unit=3, volume_rebate_pct=2),
        ],
    }
    payload.update(overrides)
    return payload


def test_tco_ranks_suppliers_on_all_in_cost():
    response = client(role="manager").post("/api/insights/supplier-tco/preview", json=body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "analysis_only"
    assert result["source"] == "user_supplied"
    assert result["requires_human_review"] is True
    assert result["actions_executed"] is False
    assert result["financial_write_performed"] is False
    assert result["company_id"] == "tenant-a"

    rows = {r["supplier_id"]: r for r in result["supplier_comparison"]}
    # Current: 10 + 1 + 0.5 duty + 0.2 + 0.4726 storage + 0.3 returns + 0.05 carbon + 0.1890 capital.
    assert rows["Andes Bottling"]["cost_per_unit"]["total"] == pytest.approx(12.7116, abs=0.0001)
    assert rows["Caribe Foods"]["cost_per_unit"]["total"] == pytest.approx(12.1414, abs=0.0001)
    assert result["supplier_comparison"][0]["supplier_id"] == "Caribe Foods"
    assert result["recommended_supplier_id"] == "Caribe Foods"
    assert result["annual_savings_vs_current"] == pytest.approx(5702.7, abs=0.5)
    assert [s["entity_id"] for s in result["esg_scores"]] == ["Andes Bottling"]
    assert 0 < result["esg_scores"][0]["scores"]["overall"] <= 100
    assert result["assumptions"]["carbon_price_usd_per_ton"] == 25


@pytest.mark.parametrize("role", ["back_office", "sales_rep", "viewer"])
def test_roles_without_financial_access_are_refused(role):
    assert client(role=role).post("/api/insights/supplier-tco/preview", json=body()).status_code == 403


def _one_supplier():
    payload = body()
    payload["suppliers"] = payload["suppliers"][:1]
    return payload


def _duplicate_names():
    payload = body()
    payload["suppliers"][1]["name"] = "Andes Bottling"
    return payload


def _bad_return_rate():
    payload = copy.deepcopy(body())
    payload["suppliers"][1]["return_rate_pct"] = 150
    return payload


def _missing_cost():
    payload = copy.deepcopy(body())
    del payload["suppliers"][1]["inbound_freight_per_unit"]
    return payload


@pytest.mark.parametrize("payload", [
    _one_supplier(),
    _duplicate_names(),
    _bad_return_rate(),
    _missing_cost(),
    body(current_supplier="Someone Else"),
    body(currency="EUR"),
    body(annual_volume=0),
    body(company_id="tenant-b"),
])
def test_invalid_input_is_422(payload):
    assert client().post("/api/insights/supplier-tco/preview", json=payload).status_code == 422
