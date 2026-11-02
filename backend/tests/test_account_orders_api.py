"""GET /api/accounts/{id}/orders: RBAC, tenant scope, 404, and 503 on outages.

Every adapter is an in-memory fake; nothing reaches QuickBooks or any network.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.auth.jwt import create_access_token
from app.auth.rbac import get_current_user
from app.connectors.base import Invoice
from app.main import app

TODAY = date.today()


def customer(qb_id: str, assigned_rep_id: str | None = None):
    # assigned_rep_id is not on the connector dataclass; a namespace mirrors
    # how a verified assignment would be attached.
    return SimpleNamespace(
        id=qb_id, qb_id=qb_id, name=f"Fixture {qb_id}", email=None, phone=None,
        state="NY", balance=Decimal("0"), total_sales=None,
        assigned_rep_id=assigned_rep_id,
    )


def invoice(inv_id: str, customer_id: str, days_ago: int = 5, *, total="100.00",
            balance="40.00", status="open", due_in: int | None = 30):
    return Invoice(
        id=inv_id, qb_id=inv_id, customer_id=customer_id, customer_name="",
        date=TODAY - timedelta(days=days_ago),
        due_date=(TODAY - timedelta(days=days_ago) + timedelta(days=due_in)) if due_in is not None else None,
        total=Decimal(total) if isinstance(total, str) else total,
        balance=Decimal(balance) if isinstance(balance, str) else balance,
        status=status, line_items=[],
    )


CUSTOMERS = [customer("QB1", assigned_rep_id="user-sales_rep"), customer("QB2")]
INVOICES = [
    invoice("inv-old", "QB1", days_ago=200, balance="0.00", status="paid"),
    invoice("inv-new", "QB1", days_ago=3),
    invoice("inv-void", "QB1", days_ago=10, balance="0.00", status="void", due_in=None),
    invoice("other-1", "QB2", days_ago=1),
]


class FakeAdapter:
    def __init__(self, customers=CUSTOMERS, invoices=INVOICES, fail=None):
        self.customers, self.invoices, self.fail = customers, invoices, fail
        self.calls = []

    async def get_all_customers(self):
        self.calls.append("customers")
        if self.fail == "customers":
            raise ConnectionError("QuickBooks unreachable secret-token")
        return self.customers

    async def get_orders_last_n_days(self, days):
        self.calls.append(("orders", days))
        if self.fail == "orders":
            raise ConnectionError("QuickBooks unreachable secret-token")
        return self.invoices


def auth(role: str, company: str = "tenant-a") -> dict:
    token = create_access_token({"sub": f"user-{role}", "company_id": company, "role": role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_adapter, None)


def use(adapters: dict, tenants: list | None = None):
    """Serve each tenant only its own adapter, keyed by the token's company."""
    def override(user: dict = Depends(get_current_user)):
        if tenants is not None:
            tenants.append(user["company_id"])
        return adapters[user["company_id"]]
    app.dependency_overrides[get_adapter] = override


def test_owner_sees_only_this_accounts_invoices_newest_first(client):
    adapter = FakeAdapter()
    tenants: list = []
    use({"tenant-a": adapter}, tenants)
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner"))
    assert response.status_code == 200, response.text
    body = response.json()

    assert tenants == ["tenant-a"]
    assert adapter.calls == ["customers", ("orders", 365)]
    assert body["account_id"] == "QB1"
    assert [o["id"] for o in body["orders"]] == ["inv-new", "inv-void", "inv-old"]
    assert body["count"] == 3 and body["total"] == 3 and body["truncated"] is False
    assert body["history_window_days"] == 365
    assert body["unmatched_invoices"] == 0

    newest = body["orders"][0]
    assert newest == {
        "id": "inv-new",
        "invoice_date": (TODAY - timedelta(days=3)).isoformat(),
        "due_date": (TODAY - timedelta(days=3) + timedelta(days=30)).isoformat(),
        "total_amount": 100.0,
        "balance_due": 40.0,
        "status": "open",
    }
    # Voided documents stay visible with their status rather than vanishing.
    assert body["orders"][1]["status"] == "void"
    assert body["orders"][1]["due_date"] is None


@pytest.mark.parametrize("role", ["owner", "manager", "back_office"])
def test_company_wide_roles_can_read_any_account(client, role):
    use({"tenant-a": FakeAdapter()})
    response = client.get("/api/accounts/QB2/orders", headers=auth(role))
    assert response.status_code == 200, response.text
    assert [o["id"] for o in response.json()["orders"]] == ["other-1"]


def test_sales_rep_reads_only_an_assigned_account(client):
    use({"tenant-a": FakeAdapter()})
    own = client.get("/api/accounts/QB1/orders", headers=auth("sales_rep"))
    assert own.status_code == 200, own.text
    assert len(own.json()["orders"]) == 3

    other = client.get("/api/accounts/QB2/orders", headers=auth("sales_rep"))
    assert other.status_code == 403
    assert "other-1" not in other.text


def test_viewer_is_rejected_before_any_source_read(client):
    adapter = FakeAdapter()
    use({"tenant-a": adapter})
    response = client.get("/api/accounts/QB1/orders", headers=auth("viewer"))
    assert response.status_code == 403
    assert adapter.calls == []


def test_unauthenticated_request_is_rejected(client):
    use({"tenant-a": FakeAdapter()})
    assert client.get("/api/accounts/QB1/orders").status_code == 401


def test_unknown_account_is_404_and_reads_no_invoices(client):
    adapter = FakeAdapter()
    use({"tenant-a": adapter})
    response = client.get("/api/accounts/NOPE/orders", headers=auth("owner"))
    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"
    assert adapter.calls == ["customers"]


def test_another_tenants_account_is_not_found(client):
    tenant_b = FakeAdapter(customers=[customer("QB9")], invoices=[invoice("b-1", "QB9")])
    use({"tenant-a": FakeAdapter(), "tenant-b": tenant_b})
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner", company="tenant-b"))
    assert response.status_code == 404
    assert "inv-new" not in response.text


@pytest.mark.parametrize("fail", ["customers", "orders"])
def test_source_outage_is_503_never_an_empty_list(client, fail):
    use({"tenant-a": FakeAdapter(fail=fail)})
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner"))
    assert response.status_code == 503
    assert "orders" not in response.json()
    assert "secret-token" not in response.text


@pytest.mark.parametrize("bad", [
    invoice("bad-total", "QB1", total=None),
    invoice("bad-total", "QB1", total=Decimal("NaN")),
    invoice("bad-balance", "QB1", balance=Decimal("-5")),
    SimpleNamespace(id="bad-date", qb_id="bad-date", customer_id="QB1", date=None,
                    due_date=None, total=Decimal("1"), balance=Decimal("0"),
                    status="open", line_items=[]),
])
def test_unreadable_invoice_for_this_account_is_503_not_a_shorter_list(client, bad):
    use({"tenant-a": FakeAdapter(invoices=[*INVOICES, bad])})
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner"))
    assert response.status_code == 503
    assert response.json()["detail"] == "Order history for this account could not be read completely"


def test_duplicate_invoice_ids_are_503(client):
    use({"tenant-a": FakeAdapter(invoices=[*INVOICES, invoice("inv-new", "QB1", days_ago=9)])})
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner"))
    assert response.status_code == 503


def test_unknown_balance_stays_unknown_not_zero(client):
    use({"tenant-a": FakeAdapter(invoices=[invoice("inv-1", "QB1", balance=None)])})
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner"))
    assert response.status_code == 200, response.text
    assert response.json()["orders"][0]["balance_due"] is None


def test_invoices_without_a_customer_are_counted_not_hidden(client):
    use({"tenant-a": FakeAdapter(invoices=[*INVOICES, invoice("orphan", "", days_ago=2)])})
    response = client.get("/api/accounts/QB1/orders", headers=auth("owner"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unmatched_invoices"] == 1
    assert "orphan" not in [o["id"] for o in body["orders"]]


def test_limit_reports_truncation(client):
    use({"tenant-a": FakeAdapter()})
    response = client.get("/api/accounts/QB1/orders?limit=2", headers=auth("owner"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [o["id"] for o in body["orders"]] == ["inv-new", "inv-void"]
    assert body["count"] == 2 and body["total"] == 3 and body["truncated"] is True


def test_account_with_no_invoices_returns_an_honest_empty_window(client):
    use({"tenant-a": FakeAdapter(invoices=[])})
    response = client.get("/api/accounts/QB2/orders", headers=auth("owner"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["orders"] == [] and body["total"] == 0
    assert body["history_window_days"] == 365
