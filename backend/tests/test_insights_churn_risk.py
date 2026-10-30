"""Churn-risk insight: complete-history screening, RBAC, outages and tenant scope."""
import ast
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.api.insights.churn_risk import HISTORY_START
from app.auth.jwt import create_access_token
from app.auth.rbac import get_current_user
from app.connectors.base import Customer, Invoice
from app.main import app

TODAY = date.today()
URL = "/api/insights/churn-risk"


def customer(qb_id: str, name: str) -> Customer:
    return Customer(
        id=qb_id, qb_id=qb_id, name=name, email=None, phone=None, state=None,
        balance=Decimal("0"), total_sales=Decimal("0"),
    )


def invoice(inv_id: str, customer_id: str, days_ago: int | None, status: str = "paid") -> Invoice:
    return Invoice(
        id=inv_id, qb_id=inv_id, customer_id=customer_id, customer_name="",
        date=TODAY - timedelta(days=days_ago) if days_ago is not None else None,
        due_date=None, total=Decimal("100.00"), balance=Decimal("0"), status=status, line_items=[],
    )


CUSTOMERS = [
    customer("QB1", "Acme Corp"),        # orders monthly -> low
    customer("QB2", "Bayside Grocers"),  # lapsed -> high
    customer("QB3", "Corner Deli"),      # slowing -> medium
    customer("QB4", "Dune Imports"),     # one order -> not enough history
    customer("QB5", "Echo Foods"),       # never ordered
    customer("QB6", "Fjord Supply"),     # one unreadable order -> incomplete
    customer("QB7", "Granite Co"),       # duplicated source id -> incomplete
]

INVOICES = (
    [invoice(f"acme-{i}", "QB1", 10 + 30 * i) for i in range(8)]
    + [invoice("acme-void", "QB1", 5, status="void")]
    + [invoice("bay-1", "QB2", 260), invoice("bay-2", "QB2", 240), invoice("bay-3", "QB2", 220)]
    + [invoice("deli-1", "QB3", 120), invoice("deli-2", "QB3", 90), invoice("deli-3", "QB3", 60)]
    + [invoice("dune-1", "QB4", 40)]
    + [invoice("fjord-1", "QB6", 50), invoice("fjord-2", "QB6", 20), invoice("fjord-bad", "QB6", None)]
    + [invoice("dup", "QB7", 30), invoice("dup", "QB7", 15), invoice("granite-1", "QB7", 70)]
    + [invoice("orphan", "", 12)]
)


class FakeAdapter:
    def __init__(self, customers=CUSTOMERS, invoices=INVOICES, fail=None):
        self.customers, self.invoices, self.fail = customers, invoices, fail
        self.calls = []

    async def get_all_customers(self):
        self.calls.append("customers")
        if self.fail == "customers":
            raise ConnectionError("QuickBooks unreachable")
        return self.customers

    async def get_orders_last_n_days(self, days):
        self.calls.append(("orders", days))
        if self.fail == "orders":
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


def test_ranks_lapsed_and_slowing_customers_with_reasons_and_next_steps(client):
    adapter = FakeAdapter()
    use(adapter)
    response = client.get(URL, headers=auth("owner"))
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False

    assert body["counts"] == {"high": 1, "medium": 1, "low": 1, "unknown": 3, "not_applicable": 1}
    assert body["customers_assessed"] == 7
    assert [c["name"] for c in body["at_risk"]] == ["Bayside Grocers", "Corner Deli"]

    bayside, deli = body["at_risk"]
    assert bayside["risk"] == "high"
    assert bayside["score"] == 0.85
    assert bayside["why"] == "No order for 220 days; they usually order about every 20 days."
    assert bayside["last_order_date"] == (TODAY - timedelta(days=220)).isoformat()
    assert bayside["orders_last_12_months"] == 3
    assert "this week" in bayside["next_step"]
    assert deli["risk"] == "medium"
    assert deli["score"] == 0.6
    assert "60 days ago" in deli["why"]

    unknown = {c["name"]: c for c in body["insufficient_data"]}
    assert set(unknown) == {"Dune Imports", "Fjord Supply", "Granite Co"}
    assert unknown["Dune Imports"]["score"] is None
    assert "Only one order date" in unknown["Dune Imports"]["why"]
    assert unknown["Fjord Supply"]["history_complete"] is False
    assert "could not be read" in unknown["Fjord Supply"]["why"]
    assert unknown["Granite Co"]["history_complete"] is False

    # Bad date, two copies of a duplicated id and one order without a customer.
    assert body["rows_skipped"] == 4
    assert body["rows_unattributed"] == 1
    # The complete source history is requested, not a recent window.
    assert ("orders", (TODAY - HISTORY_START).days) in adapter.calls


def test_voided_invoice_does_not_count_as_an_order(client):
    use(FakeAdapter(invoices=[
        invoice("a1", "QB1", 200), invoice("a2", "QB1", 180), invoice("void", "QB1", 3, status="void"),
    ], customers=[customer("QB1", "Acme Corp")]))
    body = client.get(URL, headers=auth("manager")).json()
    acme = body["at_risk"][0]
    assert acme["last_order_date"] == (TODAY - timedelta(days=180)).isoformat()
    assert acme["orders_last_12_months"] == 2


def test_back_office_may_view_customer_churn(client):
    use(FakeAdapter())
    assert client.get(URL, headers=auth("back_office")).status_code == 200


@pytest.mark.parametrize("role", ["sales_rep", "viewer"])
def test_roles_without_customer_access_are_refused_before_any_read(client, role):
    adapter = FakeAdapter()
    use(adapter)
    response = client.get(URL, headers=auth(role))
    assert response.status_code == 403
    assert adapter.calls == []


@pytest.mark.parametrize("fail", ["customers", "orders"])
def test_source_failure_is_an_outage_not_an_empty_list(client, fail):
    use(FakeAdapter(fail=fail))
    response = client.get(URL, headers=auth("owner"))
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_tenant_comes_from_the_token_not_the_request(client):
    tenants = []
    use(FakeAdapter(), tenants)
    response = client.get(URL + "?company_id=tenant-b", headers=auth("owner", "tenant-a"))
    assert response.status_code == 200
    assert tenants == ["tenant-a"]


@pytest.mark.parametrize("query", ["?limit=0", "?limit=501", "?limit=abc"])
def test_invalid_limit_is_rejected(client, query):
    use(FakeAdapter())
    assert client.get(URL + query, headers=auth("owner")).status_code == 422


def test_limit_truncates_lists(client):
    use(FakeAdapter())
    body = client.get(URL + "?limit=1", headers=auth("owner")).json()
    assert len(body["at_risk"]) == 1 and len(body["insufficient_data"]) == 1
    assert body["truncated"] is True


def test_log_lines_are_constant():
    source = (Path(__file__).resolve().parents[1] / "app/api/insights/churn_risk.py").read_text()
    for call in ast.walk(ast.parse(source)):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "log":
            assert len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
