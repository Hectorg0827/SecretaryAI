"""Exact-cent, safe cost-to-serve allocation with warehouse/landed separation."""
from dataclasses import replace
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.auth.rbac import get_current_user
from app.main import app
from app.intelligence.pricing.shipment_allocation import (
    AllocationScenario, ProductLine, ExpenseLine, allocate_shipment,
)


def products():
    return (
        ProductLine("BEER-500", D("100"), D("18"), weight_kg=D("1400"),
                    volume_cbm=D("3"), pallet_days=D("1000"), picks=D("8"),
                    discount_per_case=D("1"), commission_per_case=D("0.50")),
        ProductLine("WINE-750", D("50"), D("20"), weight_kg=D("850"),
                    volume_cbm=D("2"), pallet_days=D("500"), picks=D("4"),
                    discount_per_case=D("2"), marketing_per_case=D("0.25")),
    )


def expense(component="warehouse_storage", amount="90.00", basis="pallet_days",
            ref="invoice-1", currency="USD", evidence="warehouse-oct"):
    return ExpenseLine(ref, component, D(amount), currency, basis, evidence, "v1")


def scenario(charges=None, products_override=None):
    return AllocationScenario(
        "tenant-a", "container-42", "USD", D("30"),
        products() if products_override is None else products_override,
        (expense(),) if charges is None else charges,
    )


def test_pallet_day_storage_allocates_exactly_and_influences_price():
    result = allocate_shipment(scenario())
    assert result["currency"] == "USD"
    assert result["charges"][0]["allocated"] == {
        "BEER-500": "60.00", "WINE-750": "30.00",
    }
    beer, wine = result["products"]
    assert beer["warehouse_cost_per_case"] == "0.600000"
    assert wine["warehouse_cost_per_case"] == "0.600000"
    assert beer["minimum_list_price_at_target_contribution"] == "28.29"
    assert wine["minimum_list_price_at_target_contribution"] == "31.79"
    assert "warehouse_receiving" in result["missing_warehouse_components"]
    assert result["costs_reconciled"] is False
    assert result["prices_updated"] is False
    assert result["accounting_entries_posted"] is False


def test_inbound_and_warehouse_stay_separate():
    charges = (
        expense("international_freight", "225.00", "weight_kg", "ocean-1", evidence="forwarder-bill"),
        expense("warehouse_storage", "90.00", "pallet_days", "storage-1"),
        expense("warehouse_handling", "12.00", "picks", "pick-1", evidence="wms-tariff"),
    )
    result = allocate_shipment(scenario(charges))
    assert result["expenses_total"] == "327.00"
    assert result["products"][0]["inbound_cost_per_case"] == "19.400000"  # 140 of 225
    assert result["products"][0]["warehouse_cost_per_case"] == "0.680000"
    assert result["products"][1]["warehouse_cost_per_case"] == "0.680000"
    assert sum(D(p["warehouse_total"]) for p in result["products"]) == D("102")


def test_one_cent_remainder_is_deterministic_and_conserved():
    products_override = tuple(
        ProductLine(code, D("1"), D("1")) for code in ("A", "B", "C")
    )
    charge = expense("port_fees", "10.01", "cases")
    result = allocate_shipment(scenario((charge,), products_override))
    assert result["charges"][0]["allocated"] == {
        "A": "3.34", "B": "3.34", "C": "3.33"
    }
    assert sum(D(v) for v in result["charges"][0]["allocated"].values()) == D("10.01")


def test_storage_requires_pallet_days_and_never_falls_back_to_cases():
    with pytest.raises(ValueError, match="pallet-days"):
        allocate_shipment(scenario((expense(basis="cases"),)))
    zero_days = tuple(replace(p, pallet_days=D("0")) for p in products())
    with pytest.raises(ValueError, match="denominator"):
        allocate_shipment(scenario(products_override=zero_days))


@pytest.mark.parametrize("charges", [
    (expense(ref="same"), expense(ref="same")),
    (expense(currency="EUR"),),
    (expense(component="made_up_charge"),),
    (expense(evidence=""),),
    (expense(amount="0.001"),),
    (expense(amount="-1"),),
    (expense(amount="NaN"),),
])
def test_invalid_or_duplicate_expenses_are_rejected(charges):
    with pytest.raises((ValueError, ArithmeticError)):
        allocate_shipment(scenario(charges))


@pytest.mark.parametrize("products_override", [
    (products()[0], products()[0]),
    (replace(products()[0], accepted_cases=D("0")),),
    (replace(products()[0], accepted_cases=D("NaN")),),
])
def test_duplicate_or_invalid_accepted_cases_are_rejected(products_override):
    with pytest.raises((ValueError, ArithmeticError)):
        allocate_shipment(scenario(products_override=products_override))


def test_reject_cross_tenant_without_authoritative_company_identity():
    with pytest.raises(ValueError):
        allocate_shipment(replace(scenario(), company_id=""))


@pytest.fixture
def api_client():
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "pricing-user", "role": "owner", "company_id": "tenant-a"
    }
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_api_rejects_client_company_override_and_number_floats(api_client):
    payload = {
        "company_id": "tenant-b",
        "shipment_ref": "load-1",
        "products": [{"sku": "A", "accepted_cases": "10", "product_cost_per_case": "15"}],
        "expenses": [],
    }
    assert api_client.post("/api/insights/shipment-pricing/preview", json=payload).status_code == 422
    payload.pop("company_id")
    payload["products"][0]["product_cost_per_case"] = 15.1
    assert api_client.post("/api/insights/shipment-pricing/preview", json=payload).status_code == 422


def test_api_is_advisory_and_uses_authorized_tenant(api_client):
    payload = {
        "shipment_ref": "load-1",
        "products": [{"sku": "A", "accepted_cases": "10", "product_cost_per_case": "15",
                      "pallet_days": "5", "discount_per_case": "1"}],
        "expenses": [
            {"expense_line_ref": "storage-oct-1", "component": "warehouse_storage",
             "amount": "5.00", "currency": "USD", "allocation_basis": "pallet_days",
             "evidence_ref": "wms-report", "evidence_version": "2026-10"},
        ],
    }
    response = api_client.post("/api/insights/shipment-pricing/preview", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company_id"] == "tenant-a"
    assert body["products"][0]["warehouse_cost_per_case"] == "0.500000"
    assert body["products"][0]["minimum_list_price_at_target_contribution"] == "23.15"
    assert body["status"] == "analysis_only"
    assert body["actions_executed"] is False
    assert body["prices_updated"] is False


def test_role_without_financial_access_denied():
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "rep", "role": "sales_rep", "company_id": "tenant-a"
    }
    try:
        response = TestClient(app).post("/api/insights/shipment-pricing/preview", json={
            "shipment_ref": "shipment-1",
            "products": [{"sku": "A", "accepted_cases": "10", "product_cost_per_case": "15"}],
            "expenses": [],
        })
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
