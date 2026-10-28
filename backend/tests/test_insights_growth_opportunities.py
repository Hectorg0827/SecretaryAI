"""Growth opportunities: connected signals classified by the taxonomy, per role."""
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.api.insights import growth_opportunities
from app.auth.rbac import get_current_user
from app.connectors.base import Invoice
from app.intelligence.growth.opportunity_taxonomy import classify_growth_signal
from app.main import app

URL = "/api/insights/growth-opportunities"


def _inventory():
    def row(qb_id, name, sku, qty, cost, price=30.0):
        return {"qb_id": qb_id, "product_name": name, "sku": sku, "total_qty": qty,
                "purchase_cost": cost, "unit_price": price, "source_quality": "partial"}
    return [
        row("ITEM-A", "Valley Red 12x750ml", "VR-12", 60.0, 20.0),
        row("ITEM-B", "Coast White 12x750ml", "CW-12", 0.0, 18.0, 26.0),
        row("ITEM-C", "Gift Box", "GB-1", 10.0, 0.0, 20.0),
        row("ITEM-D", "Old Rose 6x750ml", "OR-6", 40.0, 15.0),
        row("ITEM-E", "Slow Sparkling 6x750ml", "SS-6", 200.0, 10.0),
        {"qb_id": None, "product_name": "Broken row"},
    ]


def _invoices():
    today = date.today()

    def inv(inv_id, customer, days_ago, lines, status="paid"):
        return Invoice(id=inv_id, qb_id=inv_id, customer_id=customer, customer_name=customer,
                       date=today - timedelta(days=days_ago), due_date=None, total=0, balance=0,
                       status=status, line_items=lines)

    def line(item_id, qty, amount, name=""):
        return {"item_id": item_id, "name": name, "quantity": qty, "amount": amount}

    return [
        inv("INV-1", "C1", 3, [line("ITEM-A", 20, 600), line("ITEM-B", 10, 260),
                               line("SVC-1", 1, 25, "Delivery fee")]),
        inv("INV-2", "C2", 10, [line("ITEM-A", 10, 300)]),
        inv("INV-3", "C1", 20, [line("ITEM-A", 10, 300), line("ITEM-A", -2, -60)]),
        inv("INV-4", "C3", 5, [line("ITEM-C", 5, 100)]),
        inv("INV-V", "C2", 4, [line("ITEM-A", 100, 3000)], status="voided"),
        {"id": "INV-BAD", "customer_id": "C9", "date": "not-a-date", "line_items": []},
        inv("INV-5", "C1", 40, [line("ITEM-A", 30, 900)]),
        inv("INV-6", "C2", 45, [line("ITEM-B", 20, 520)]),
        inv("INV-7", "C3", 60, [line("ITEM-E", 13, 140)]),
    ]


def _adapter():
    adapter = AsyncMock()
    adapter.get_inventory_merged.return_value = _inventory()
    adapter.get_orders_last_n_days.return_value = _invoices()
    return adapter


@pytest.fixture
def client():
    def make(role="owner", company_id="tenant-a", adapter=None):
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-1", "company_id": company_id, "role": role,
        }
        app.dependency_overrides[get_adapter] = lambda: adapter or _adapter()
        return TestClient(app)

    yield make
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_adapter, None)


def _entries(body, section):
    return [(e["signal_code"], e["entity_ref"]) for e in body["sections"][section]]


def test_owner_sees_every_section_from_connected_sales(client):
    adapter = _adapter()
    response = client(adapter=adapter).get(URL, params={"min_margin_pct": 31})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "analysis_only" and body["source"] == "connected_data"
    assert body["requires_human_review"] is True and body["actions_executed"] is False
    adapter.get_orders_last_n_days.assert_awaited_once_with(56)

    assert _entries(body, "scoreboard") == [("brand_growth", "ITEM-A")]
    assert _entries(body, "yellow_alerts") == [("brand_decline", "ITEM-B")]
    assert sorted(_entries(body, "red_alerts")) == [
        ("margin_breach", "ITEM-B"), ("portfolio_stockout_with_demand", "ITEM-B"),
    ]
    growth = body["sections"]["scoreboard"][0]
    assert growth["display_metric"] == 26.7
    assert growth["status"] == "needs_review"
    assert growth["next_action_code"] == "review_growth_evidence"
    assert growth["product_name"] == "Valley Red 12x750ml"
    assert body["sections"]["yellow_alerts"][0]["display_metric"] == -50.0
    red = {e["signal_code"]: e for e in body["sections"]["red_alerts"]}
    assert red["margin_breach"]["display_metric"] == 30.8
    # Stock counts are only "partial" from the adapter: the alert stays red,
    # but no metric is shown and the next step is to verify the source.
    stockout = red["portfolio_stockout_with_demand"]
    assert stockout["status"] == "source_unavailable"
    assert stockout["display_metric"] is None
    assert stockout["next_action_code"] == "verify_source_evidence"
    assert all(e["approval_required"] for entries in body["sections"].values() for e in entries)
    assert body["signals_total"] == 4
    assert body["rows_skipped"] == 2


def test_default_thresholds_do_not_flag_healthy_margins(client):
    body = client().get(URL).json()
    assert ("margin_breach", "ITEM-B") not in _entries(body, "red_alerts")
    assert body["thresholds"] == {"change_pct": 20, "min_margin_pct": 15}


def test_back_office_only_sees_operations_signals(client):
    response = client(role="back_office").get(URL, params={"min_margin_pct": 31})
    assert response.status_code == 200
    body = response.json()
    assert _entries(body, "red_alerts") == [("portfolio_stockout_with_demand", "ITEM-B")]
    assert body["sections"]["scoreboard"] == []
    assert body["sections"]["yellow_alerts"] == []
    assert body["signals_total"] == 1


@pytest.mark.parametrize("role", ["sales_rep", "viewer"])
def test_roles_without_reports_are_refused(client, role):
    assert client(role=role).get(URL).status_code == 403


@pytest.mark.parametrize("method", ["get_inventory_merged", "get_orders_last_n_days"])
def test_source_failure_is_an_outage(client, method):
    adapter = _adapter()
    getattr(adapter, method).side_effect = TimeoutError("provider timeout")
    response = client(adapter=adapter).get(URL)
    assert response.status_code == 503
    assert "sections" not in response.json()


@pytest.mark.parametrize("params", [{"weeks": 0}, {"change_threshold_pct": 4}, {"min_margin_pct": 91}])
def test_invalid_parameters_are_rejected(client, params):
    assert client().get(URL, params=params).status_code == 422


def test_scope_and_tenant_come_from_the_token(client):
    with patch.object(growth_opportunities, "classify_growth_signal", wraps=classify_growth_signal) as spy:
        response = client(company_id="tenant-a", role="manager").get(URL, params={"company_id": "tenant-b"})
    assert response.status_code == 200
    assert spy.call_count == 3
    for call in spy.call_args_list:
        signal, scope = call.args[0], call.kwargs["scope"]
        assert signal.company_id == scope.company_id == "tenant-a"
        assert scope.effective_role == "manager"
