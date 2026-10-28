"""Anomalies insight: unusual orders and weeks, RBAC, outages and tenant scope."""
import ast
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.auth.jwt import create_access_token
from app.auth.rbac import get_current_user
from app.connectors.base import Invoice
from app.intelligence.anomaly_detection import detect_order_anomalies
from app.main import app

TODAY = date.today()
URL = "/api/insights/anomalies"


def week_order(inv_id, customer_id, name, total, week, status="paid"):
    """An order two days before the end of complete week `week` (0 = most recent)."""
    return Invoice(
        id=inv_id, qb_id=inv_id, customer_id=customer_id, customer_name=name,
        date=TODAY - timedelta(days=3 + 7 * week), due_date=None,
        total=Decimal(total) if total is not None else None,
        balance=Decimal("0"), status=status, line_items=[],
    )


# Bayside orders 1,000 every week; Acme orders 100 every week except one
# 5,000 order in week 2, which also lifts that week's revenue.
INVOICES = (
    [week_order(f"bay-{w}", "QB2", "Bayside Grocers", "1000.00", w) for w in range(12)]
    + [week_order(f"acme-{w}", "QB1", "Acme Corp", "5000.00" if w == 2 else "100.00", w) for w in range(12)]
    + [week_order("acme-void", "QB1", "Acme Corp", "0.00", 5, status="void")]
)


class FakeAdapter:
    def __init__(self, invoices=INVOICES, fail=False):
        self.invoices, self.fail = invoices, fail
        self.calls = []

    async def get_orders_last_n_days(self, days):
        self.calls.append(("orders", days))
        if self.fail:
            raise ConnectionError("QuickBooks unreachable")
        return self.invoices


def auth(role: str, company: str = "tenant-a") -> dict:
    token = create_access_token({"sub": f"user-{role}", "company_id": company, "role": role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_adapter, None)


def use(adapter, tenants=None):
    def override(user: dict = Depends(get_current_user)):
        if tenants is not None:
            tenants.append(user["company_id"])
        return adapter
    app.dependency_overrides[get_adapter] = override


def test_flags_unusual_customer_order_and_revenue_week(client):
    adapter = FakeAdapter()
    use(adapter)
    response = client.get(URL, headers=auth("owner"))
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["requires_human_review"] is True
    assert adapter.calls == [("orders", 90)]

    assert body["weeks_compared"] == 12
    assert body["orders_analyzed"] == 24  # the voided invoice is not an order
    assert body["customers_checked"] == 2
    assert body["weekly_trends"] == "checked"
    assert body["rows_skipped"] == 0
    assert body["counts"] == {"high": 2, "medium": 0}

    order, week = body["anomalies"]
    assert order["kind"] == "customer_order"
    assert (order["customer"], order["customer_id"], order["order_id"]) == ("Acme Corp", "QB1", "acme-2")
    assert (order["direction"], order["severity"], order["value"]) == ("high", "high", 5000.0)
    assert order["date"] == (TODAY - timedelta(days=17)).isoformat()
    assert order["usual_low"] == 0.0
    assert order["usual_high"] == pytest.approx(3337.35, abs=0.01)  # mean 508.33 + 2 x 1414.51
    assert "Confirm quantities" in order["suggested_check"]

    assert week["kind"] == "weekly_revenue"
    assert (week["direction"], week["severity"], week["value"]) == ("high", "high", 6000.0)
    assert week["period_start"] == (TODAY - timedelta(days=21)).isoformat()
    assert week["period_end"] == (TODAY - timedelta(days=15)).isoformat()


def test_unreadable_order_is_skipped_and_withholds_weekly_totals(client):
    use(FakeAdapter(invoices=INVOICES + [week_order("deli-1", "QB3", "Corner Deli", None, 1)]))
    body = client.get(URL, headers=auth("owner")).json()
    assert body["rows_skipped"] == 1
    assert body["weekly_trends"] == "withheld"
    assert [a["kind"] for a in body["anomalies"]] == ["customer_order"]


def test_same_named_customers_are_never_merged(client):
    use(FakeAdapter(invoices=(
        [week_order(f"m8-{w}", "QB8", "Metro Distributors", "100.00", w) for w in range(11)]
        + [week_order("m9-1", "QB9", "Metro Distributors", "5000.00", 0)]
    )))
    body = client.get(URL, headers=auth("manager")).json()
    assert [a for a in body["anomalies"] if a["kind"] == "customer_order"] == []
    assert body["customers_checked"] == 1
    assert body["customers_with_too_few_orders"] == 1


@pytest.mark.parametrize("role", ["back_office", "sales_rep", "viewer"])
def test_revenue_anomalies_are_owner_and_manager_only(client, role):
    adapter = FakeAdapter()
    use(adapter)
    assert client.get(URL, headers=auth(role)).status_code == 403
    assert adapter.calls == []


def test_source_failure_is_an_outage_not_no_anomalies(client):
    use(FakeAdapter(fail=True))
    response = client.get(URL, headers=auth("owner"))
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_tenant_comes_from_the_token_not_the_request(client):
    tenants = []
    use(FakeAdapter(), tenants)
    response = client.get(URL + "?company_id=tenant-b", headers=auth("owner", "tenant-a"))
    assert response.status_code == 200
    assert tenants == ["tenant-a"]


@pytest.mark.parametrize("query", ["?lookback_days=7", "?lookback_days=400", "?lookback_days=x"])
def test_invalid_window_is_rejected(client, query):
    use(FakeAdapter())
    assert client.get(URL + query, headers=auth("owner")).status_code == 422


def _rows(amounts, days_ago=1):
    return [
        {"customer_name": "Acme", "total_amount": amount,
         "order_date": (TODAY - timedelta(days=days_ago + i)).isoformat()}
        for i, amount in enumerate(amounts)
    ]


def test_detector_only_flags_orders_inside_its_baseline_window():
    rows = _rows([100] * 7 + [120]) + _rows([100_000], days_ago=200)
    assert detect_order_anomalies(rows, today=TODAY) == []


def test_detector_never_treats_a_missing_amount_as_a_zero_order():
    rows = _rows([1000] * 9) + [{"customer_name": "Acme", "order_date": TODAY.isoformat()}]
    assert detect_order_anomalies(rows, today=TODAY) == []


def test_log_lines_are_constant():
    source = (Path(__file__).resolve().parents[1] / "app/api/insights/anomalies.py").read_text()
    for call in ast.walk(ast.parse(source)):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "log":
            assert len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
