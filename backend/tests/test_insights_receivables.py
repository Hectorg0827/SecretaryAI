"""Receivables insight: aging, collection order, RBAC, outages and tenant scope."""
import ast
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.api.insights.receivables import HISTORY_START
from app.auth.jwt import create_access_token
from app.auth.rbac import get_current_user
from app.connectors.base import Customer, Invoice
from app.main import app

TODAY = date.today()
URL = "/api/insights/receivables"


def customer(qb_id: str, name: str, balance: str) -> Customer:
    return Customer(
        id=qb_id, qb_id=qb_id, name=name, email=None, phone=None, state=None,
        balance=Decimal(balance), total_sales=Decimal("0"),
    )


def invoice(inv_id, customer_id, name, total, balance, due_in_days, status="open"):
    return Invoice(
        id=inv_id, qb_id=inv_id, customer_id=customer_id, customer_name=name,
        date=TODAY - timedelta(days=150),
        due_date=TODAY + timedelta(days=due_in_days) if due_in_days is not None else None,
        total=Decimal(total), balance=Decimal(balance), status=status, line_items=[],
    )


CUSTOMERS = [
    customer("QB1", "Acme Corp", "1500.00"),
    customer("QB2", "Bayside Grocers", "800.00"),
    customer("QB3", "Corner Deli", "250.00"),     # a 50.00 credit in QuickBooks
    customer("QB4", "Dune Imports", "0.00"),
    customer("QB5", "Echo Foods", "900.00"),      # has an unreadable invoice
    customer("QB6", "Fjord Supply", "450.00"),    # balance without open invoices
    customer("QB7", "Granite Co", "200.00"),      # invoice without a due date
]

INVOICES = [
    invoice("a1", "QB1", "Acme Corp", "1000.00", "1000.00", 10),
    invoice("a2", "QB1", "Acme Corp", "500.00", "500.00", -75),
    invoice("b1", "QB2", "Bayside Grocers", "1200.00", "800.00", -40),
    invoice("c1", "QB3", "Corner Deli", "300.00", "300.00", -5),
    invoice("d1", "QB4", "Dune Imports", "400.00", "0.00", -90, status="paid"),
    invoice("e1", "QB5", "Echo Foods", "900.00", "900.00", -120),
    invoice("e2", "QB5", "Echo Foods", "100.00", "250.00", -10),  # balance exceeds total
    invoice("g1", "QB7", "Granite Co", "200.00", "200.00", None),
]


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
        self.calls.append(("invoices", days))
        if self.fail == "invoices":
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


def test_ages_balances_and_orders_collections_by_oldest_overdue(client):
    adapter = FakeAdapter()
    use(adapter)
    response = client.get(URL + "?currency=USD", headers=auth("owner"))
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["actions_executed"] is False
    assert body["currency"] == "USD"

    assert body["aging_status"] == "partial_aging"
    assert "missing_due_date" in body["exceptions"]
    assert body["total_owed"] == 2800.0
    assert body["overdue_total"] == 1600.0
    assert body["buckets"] == {
        "current": 1000.0, "days_1_30": 300.0, "days_31_60": 800.0,
        "days_61_90": 500.0, "days_91_plus": 0.0, "unaged": 200.0,
    }
    assert body["open_invoice_count"] == 5
    assert body["rows_skipped"] == 1
    assert body["rows_unattributed"] == 0
    assert body["reconciliation_status"] == "not_reconciled_to_payments_or_gl"

    accounts = body["accounts"]
    assert [a["name"] for a in accounts] == ["Acme Corp", "Bayside Grocers", "Corner Deli", "Granite Co"]
    acme, bayside, deli, granite = accounts
    assert (acme["priority"], acme["total_owed"], acme["overdue"]) == ("high", 1500.0, 500.0)
    assert acme["oldest_days_overdue"] == 75
    assert acme["balance_matches_quickbooks"] is True
    assert "60 days overdue" in acme["next_step"]
    assert (bayside["priority"], bayside["overdue"], bayside["oldest_days_overdue"]) == ("medium", 800.0, 40)
    assert deli["priority"] == "low"
    assert deli["quickbooks_balance"] == 250.0
    assert deli["balance_matches_quickbooks"] is False
    assert granite["priority"] is None
    assert granite["buckets"]["unaged"] == 200.0
    assert "no due date" in granite["next_step"]

    not_aged = {c["name"]: c for c in body["not_aged"]}
    assert set(not_aged) == {"Echo Foods", "Fjord Supply"}
    assert not_aged["Echo Foods"]["quickbooks_balance"] == 900.0
    assert "could not be read" in not_aged["Echo Foods"]["reason"]
    assert "no open invoices" in not_aged["Fjord Supply"]["reason"]

    # Every open invoice is in scope, not only a recent window.
    assert ("invoices", (TODAY - HISTORY_START).days) in adapter.calls


def test_without_confirmed_currency_no_amount_is_shown_and_nothing_is_read(client):
    adapter = FakeAdapter()
    use(adapter)
    body = client.get(URL, headers=auth("owner")).json()
    assert body["aging_status"] == "unavailable"
    assert body["exceptions"] == ["currency_unverified"]
    assert body["total_owed"] is None and body["buckets"] is None
    assert body["status"] == "analysis_only"
    assert adapter.calls == []


def test_duplicated_invoice_id_moves_the_customer_to_not_aged(client):
    use(FakeAdapter(invoices=[
        invoice("a1", "QB1", "Acme Corp", "700.00", "700.00", -20),
        invoice("a1", "QB1", "Acme Corp", "700.00", "700.00", -20),
        invoice("b1", "QB2", "Bayside Grocers", "800.00", "800.00", -40),
    ]))
    body = client.get(URL + "?currency=USD", headers=auth("manager")).json()
    assert body["total_owed"] == 800.0
    assert body["rows_skipped"] == 2
    assert [a["name"] for a in body["accounts"]] == ["Bayside Grocers"]
    assert "Acme Corp" in {c["name"] for c in body["not_aged"]}


def test_unreadable_invoice_without_a_customer_is_counted_not_hidden(client):
    use(FakeAdapter(invoices=[
        invoice("b1", "QB2", "Bayside Grocers", "800.00", "800.00", -40),
        invoice("x1", "", "", "300.00", "300.00", -10),
    ]))
    body = client.get(URL + "?currency=USD", headers=auth("owner")).json()
    assert body["total_owed"] == 800.0
    assert body["rows_skipped"] == 1
    assert body["rows_unattributed"] == 1


@pytest.mark.parametrize("role", ["back_office", "sales_rep", "viewer"])
def test_financial_data_is_owner_and_manager_only(client, role):
    adapter = FakeAdapter()
    use(adapter)
    assert client.get(URL + "?currency=USD", headers=auth(role)).status_code == 403
    assert adapter.calls == []


@pytest.mark.parametrize("fail", ["customers", "invoices"])
def test_source_failure_is_an_outage_not_zero_owed(client, fail):
    use(FakeAdapter(fail=fail))
    response = client.get(URL + "?currency=USD", headers=auth("owner"))
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_tenant_comes_from_the_token_not_the_request(client):
    tenants = []
    use(FakeAdapter(), tenants)
    response = client.get(URL + "?currency=USD&company_id=tenant-b", headers=auth("owner", "tenant-a"))
    assert response.status_code == 200
    assert tenants == ["tenant-a"]


@pytest.mark.parametrize("query", ["?currency=usd", "?currency=US", "?currency=USDX", "?currency=USD&limit=0"])
def test_invalid_input_is_rejected(client, query):
    use(FakeAdapter())
    assert client.get(URL + query, headers=auth("owner")).status_code == 422


def test_log_lines_are_constant():
    source = (Path(__file__).resolve().parents[1] / "app/api/insights/receivables.py").read_text()
    for call in ast.walk(ast.parse(source)):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "log":
            assert len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
