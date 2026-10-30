"""Owner-only checkout and customer-portal safety.

Every provider call goes to a synthetic stand-in for the Stripe module, so no
test can reach Stripe, create a customer or start a real payment.
"""
import ast
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import billing

FRONTEND = "https://app.synthetic.example"
LEAK = "cus_LEAKED req_LEAKED sk_test_LEAKED\r\nFORGED_LOG_EVENT"


class FakeQuery:
    """Records the filters applied to one table query and returns canned rows."""

    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.db.queries.append((self.table, tuple(self.filters)))
        rows = self.db.rows.get(self.table, [])
        if isinstance(rows, Exception):
            raise rows
        return SimpleNamespace(data=rows)


class FakeDB:
    def __init__(self, **rows):
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []
        self.rpc_calls = []
        self.checkout_attempt = None
        self.lock = threading.Lock()

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        with self.lock:
            self.rpc_calls.append((name, params))
            if name == "billing_reserve_checkout":
                if self.checkout_attempt is None:
                    self.checkout_attempt = {
                        "state": "reserved", "attempt_id": "00000000-0000-4000-8000-000000000001",
                        "stripe_customer_id": None,
                        "created_at": datetime.now(tz=timezone.utc).isoformat(),
                    }
                result = dict(self.checkout_attempt)
            elif name == "billing_finalize_checkout":
                if self.checkout_attempt and params["p_attempt_id"] == self.checkout_attempt["attempt_id"]:
                    self.checkout_attempt.update({
                        "state": "ready", "stripe_session_id": params["p_session_id"],
                        "checkout_url": params["p_checkout_url"],
                        "stripe_customer_id": params["p_customer_id"],
                    })
                    result = True
                else:
                    result = False
            elif name == "billing_rotate_expired_checkout":
                result = True
                self.checkout_attempt = {
                    "state": "reserved", "attempt_id": "00000000-0000-4000-8000-000000000002",
                    "stripe_customer_id": None,
                    "created_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            else:
                raise AssertionError(f"Unexpected RPC {name}")
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=result))

    def companies_queried(self):
        """Every tenant id used to filter a company-scoped table."""
        return {value for table, filters in self.queries if table != "subscription_plans"
                for column, value in filters if column in ("company_id", "id")}


PLAN = {"id": "growth", "stripe_price_id_monthly": "price_month", "stripe_price_id_yearly": "price_year"}


def fake_stripe():
    return SimpleNamespace(
        StripeError=Exception,
        Customer=SimpleNamespace(create=MagicMock(return_value={"id": "cus_new"})),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test_synthetic",
                                         id="cs_test_synthetic")),
            retrieve=MagicMock(return_value=SimpleNamespace(status="open")))),
        billing_portal=SimpleNamespace(Session=SimpleNamespace(create=MagicMock(
            return_value=SimpleNamespace(url="https://billing.stripe.com/p/session/test_synthetic")))),
    )


@pytest.fixture
def stripe(monkeypatch):
    fake = fake_stripe()
    monkeypatch.setattr(billing, "_stripe", fake)
    monkeypatch.setattr(billing, "_stripe_available", True)
    monkeypatch.setattr(billing.settings, "frontend_url", FRONTEND + "/")
    return fake


def client_for(db, role="owner", company_id="tenant-a"):
    app = FastAPI()
    app.include_router(billing.router, prefix="/api/billing")
    app.dependency_overrides[billing.get_current_user] = lambda: {
        "sub": "user-a", "company_id": company_id, "role": role,
    }
    app.dependency_overrides[billing.get_db] = lambda: db
    return TestClient(app)


def checkout(client, **body):
    return client.post("/api/billing/checkout", json={"plan_id": "growth", "billing_period": "monthly", **body})


# ── Who may pay ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["manager", "back_office", "sales_rep", "viewer", "unknown"])
@pytest.mark.parametrize("path", ["/api/billing/checkout", "/api/billing/portal"])
def test_only_the_owner_can_start_checkout_or_open_the_portal(stripe, role, path):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[{"stripe_customer_id": "cus_a"}])
    response = client_for(db, role=role).post(path, json={"plan_id": "growth", "billing_period": "monthly"})
    assert response.status_code == 403
    assert db.queries == []
    stripe.checkout.Session.create.assert_not_called()
    stripe.billing_portal.Session.create.assert_not_called()
    stripe.Customer.create.assert_not_called()


def test_manager_passes_view_all_but_is_still_refused(stripe):
    # require_permission("manage_billing") alone would admit managers via view_all.
    response = checkout(client_for(FakeDB(subscription_plans=[PLAN]), role="manager"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Only the account owner can manage billing"


# ── Checkout ──────────────────────────────────────────────────────────────────

def test_owner_checkout_returns_the_provider_url(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[{"email": "owner@a.example"}])
    response = checkout(client_for(db))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_synthetic",
        "session_id": "cs_test_synthetic",
    }
    kwargs = stripe.checkout.Session.create.call_args.kwargs
    assert kwargs["line_items"] == [{"price": "price_month", "quantity": 1}]
    assert kwargs["mode"] == "subscription"
    assert kwargs["success_url"] == f"{FRONTEND}/settings?billing=success"
    assert kwargs["cancel_url"] == f"{FRONTEND}/settings?billing=cancelled"
    assert kwargs["customer"] == "cus_new"
    assert kwargs["idempotency_key"] == "secretaryai-checkout-00000000-0000-4000-8000-000000000001"
    assert kwargs["metadata"]["checkout_attempt_id"] == "00000000-0000-4000-8000-000000000001"
    stripe.Customer.create.assert_called_once_with(
        email="owner@a.example", metadata={"company_id": "tenant-a"},
        idempotency_key="secretaryai-customer-00000000-0000-4000-8000-000000000001")


def test_existing_customer_is_reused_for_a_new_subscription(stripe):
    db = FakeDB(subscription_plans=[PLAN], companies=[],
                company_subscriptions=[{"status": "canceled", "stripe_customer_id": "cus_a",
                                        "stripe_subscription_id": "sub_old"}])
    response = checkout(client_for(db), billing_period="yearly")
    assert response.status_code == 200, response.text
    kwargs = stripe.checkout.Session.create.call_args.kwargs
    assert kwargs["customer"] == "cus_a"
    assert kwargs["line_items"][0]["price"] == "price_year"
    stripe.Customer.create.assert_not_called()


def test_checkout_company_comes_only_from_the_token(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    response = checkout(client_for(db, company_id="tenant-a"), company_id="tenant-b")
    assert response.status_code == 200, response.text
    assert stripe.checkout.Session.create.call_args.kwargs["metadata"] == {
        "company_id": "tenant-a", "plan_id": "growth", "billing_period": "monthly",
        "checkout_attempt_id": "00000000-0000-4000-8000-000000000001",
    }
    assert stripe.Customer.create.call_args.kwargs["metadata"] == {"company_id": "tenant-a"}
    assert db.companies_queried() == {"tenant-a"}


def test_repeated_checkout_reuses_verified_open_session_without_another_create(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    client = client_for(db)
    first = checkout(client)
    second = checkout(client)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    stripe.checkout.Session.create.assert_called_once()
    stripe.Customer.create.assert_called_once()
    stripe.checkout.Session.retrieve.assert_called_once_with("cs_test_synthetic")


def test_expired_checkout_is_replaced_only_after_stripe_confirms_it(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    client = client_for(db)
    assert checkout(client).status_code == 200
    stripe.checkout.Session.retrieve.return_value = SimpleNamespace(status="expired")
    assert checkout(client).status_code == 200
    assert stripe.checkout.Session.create.call_count == 2
    keys = [call.kwargs["idempotency_key"] for call in stripe.checkout.Session.create.call_args_list]
    assert len(set(keys)) == 2
    assert any(name == "billing_rotate_expired_checkout" for name, _ in db.rpc_calls)


def test_completed_checkout_is_not_reopened(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    client = client_for(db)
    assert checkout(client).status_code == 200
    stripe.checkout.Session.retrieve.return_value = SimpleNamespace(status="complete")
    assert checkout(client).status_code == 409
    stripe.checkout.Session.create.assert_called_once()


def test_checkout_fails_closed_when_reservation_rpc_unavailable(stripe, monkeypatch):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    def offline(*_args):
        raise RuntimeError(LEAK)
    monkeypatch.setattr(db, "rpc", offline)
    response = checkout(client_for(db))
    assert response.status_code == 503
    assert "LEAKED" not in response.text
    stripe.Customer.create.assert_not_called()
    stripe.checkout.Session.create.assert_not_called()


def test_old_uncertain_reservation_cannot_outlive_stripe_idempotency_window(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    db.checkout_attempt = {
        "state": "reserved", "attempt_id": "00000000-0000-4000-8000-000000000001",
        "created_at": (datetime.now(tz=timezone.utc) - timedelta(hours=21)).isoformat(),
    }
    response = checkout(client_for(db))
    assert response.status_code == 409
    stripe.Customer.create.assert_not_called()
    stripe.checkout.Session.create.assert_not_called()


def test_concurrent_checkout_uses_only_one_stable_idempotency_key(stripe):
    from concurrent.futures import ThreadPoolExecutor
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: checkout(client_for(db)), range(4)))
    assert all(r.status_code in (200, 409) for r in responses)
    keys = {call.kwargs["idempotency_key"] for call in stripe.checkout.Session.create.call_args_list}
    assert len(keys) == 1, "Simultaneous requests must use one Stripe idempotency key"


@pytest.mark.parametrize("status", ["active", "trialing", "past_due"])
def test_checkout_refuses_a_second_billed_subscription(stripe, status):
    db = FakeDB(subscription_plans=[PLAN], companies=[],
                company_subscriptions=[{"status": status, "stripe_customer_id": "cus_a",
                                        "stripe_subscription_id": "sub_a"}])
    response = checkout(client_for(db))
    assert response.status_code == 409
    assert "Manage subscription" in response.json()["detail"]
    stripe.checkout.Session.create.assert_not_called()


def test_local_trial_without_stripe_subscription_can_check_out(stripe):
    db = FakeDB(subscription_plans=[PLAN], companies=[],
                company_subscriptions=[{"status": "trialing", "stripe_customer_id": None,
                                        "stripe_subscription_id": None}])
    assert checkout(client_for(db)).status_code == 200


@pytest.mark.parametrize("error", [Exception(LEAK), RuntimeError(LEAK)])
def test_checkout_provider_failure_is_a_clean_error(stripe, caplog, error):
    stripe.checkout.Session.create.side_effect = error
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    with caplog.at_level(logging.DEBUG, logger=billing.log.name):
        response = checkout(client_for(db))
    assert response.status_code == 502
    assert response.json() == {"detail": billing._CHECKOUT_PROVIDER_FAILED}
    assert "LEAKED" not in response.text
    assert caplog.records, "the failure must still be logged"
    for record in caplog.records:
        assert "LEAKED" not in record.getMessage() and "FORGED" not in record.getMessage()
        assert record.exc_info is None


def test_customer_creation_failure_is_a_clean_error(stripe, caplog):
    stripe.Customer.create.side_effect = Exception(LEAK)
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    with caplog.at_level(logging.DEBUG, logger=billing.log.name):
        response = checkout(client_for(db))
    assert response.status_code == 502
    assert "LEAKED" not in response.text
    assert not any("LEAKED" in record.getMessage() for record in caplog.records)
    stripe.checkout.Session.create.assert_not_called()


@pytest.mark.parametrize("url", [None, "", "http://checkout.stripe.com/x", "javascript:alert(1)"])
def test_checkout_without_a_usable_url_is_not_reported_as_success(stripe, url):
    stripe.checkout.Session.create.return_value = SimpleNamespace(url=url, id="cs_test")
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    response = checkout(client_for(db))
    assert response.status_code == 502
    assert "checkout_url" not in response.json()


def test_checkout_without_stripe_configured_fails_instead_of_faking_a_url(stripe, monkeypatch):
    monkeypatch.setattr(billing, "_stripe_available", False)
    response = checkout(client_for(FakeDB(subscription_plans=[PLAN])))
    assert response.status_code == 503
    assert response.json() == {"detail": billing._PAYMENTS_NOT_SET_UP}
    assert "mock" not in response.text


def test_checkout_without_a_return_url_in_production_fails_closed(stripe, monkeypatch):
    monkeypatch.setattr(billing.settings, "frontend_url", "")
    monkeypatch.setattr(billing.settings, "debug", False)
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=[], companies=[])
    response = checkout(client_for(db))
    assert response.status_code == 503
    stripe.checkout.Session.create.assert_not_called()
    stripe.Customer.create.assert_not_called()


def test_unknown_plan_and_bad_period_are_refused(stripe):
    db = FakeDB(subscription_plans=[], company_subscriptions=[], companies=[])
    response = checkout(client_for(db), plan_id="missing'\nplan")
    assert response.status_code == 404
    assert response.json() == {"detail": "That plan is not available"}
    response = checkout(client_for(FakeDB(subscription_plans=[PLAN])), billing_period="weekly")
    assert response.status_code == 422
    stripe.checkout.Session.create.assert_not_called()


def test_plan_without_online_price_is_refused(stripe):
    db = FakeDB(subscription_plans=[{**PLAN, "stripe_price_id_monthly": None}],
                company_subscriptions=[], companies=[])
    response = checkout(client_for(db))
    assert response.status_code == 400
    stripe.checkout.Session.create.assert_not_called()


def test_unreadable_subscription_record_does_not_allow_checkout(stripe):
    db = FakeDB(subscription_plans=[PLAN], company_subscriptions=None, companies=[])
    response = checkout(client_for(db))
    assert response.status_code == 503
    stripe.checkout.Session.create.assert_not_called()
    stripe.Customer.create.assert_not_called()


def test_billing_database_outage_is_a_clean_error(stripe, caplog):
    db = FakeDB(subscription_plans=RuntimeError(LEAK))
    with caplog.at_level(logging.DEBUG, logger=billing.log.name):
        response = checkout(client_for(db))
    assert response.status_code == 503
    assert response.json() == {"detail": billing._BILLING_RECORDS_UNAVAILABLE}
    assert not any("LEAKED" in record.getMessage() for record in caplog.records)
    stripe.checkout.Session.create.assert_not_called()


# ── Customer portal ───────────────────────────────────────────────────────────

def test_owner_portal_returns_the_provider_url(stripe):
    db = FakeDB(company_subscriptions=[{"status": "active", "stripe_customer_id": "cus_a",
                                        "stripe_subscription_id": "sub_a"}])
    response = client_for(db).post("/api/billing/portal")
    assert response.status_code == 200, response.text
    assert response.json() == {"portal_url": "https://billing.stripe.com/p/session/test_synthetic"}
    stripe.billing_portal.Session.create.assert_called_once_with(
        customer="cus_a", return_url=f"{FRONTEND}/settings?billing=portal")
    assert db.queries == [("company_subscriptions", (("company_id", "tenant-a"),))]


def test_portal_company_comes_only_from_the_token(stripe):
    db = FakeDB(company_subscriptions=[{"stripe_customer_id": "cus_a"}])
    response = client_for(db, company_id="tenant-a").post(
        "/api/billing/portal", json={"company_id": "tenant-b"})
    assert response.status_code == 200
    assert db.companies_queried() == {"tenant-a"}


def test_portal_without_a_customer_is_refused(stripe):
    response = client_for(FakeDB(company_subscriptions=[])).post("/api/billing/portal")
    assert response.status_code == 404
    assert "no paid subscription" in response.json()["detail"]
    stripe.billing_portal.Session.create.assert_not_called()


def test_portal_provider_failure_is_a_clean_error(stripe, caplog):
    stripe.billing_portal.Session.create.side_effect = Exception(LEAK)
    db = FakeDB(company_subscriptions=[{"stripe_customer_id": "cus_a"}])
    with caplog.at_level(logging.DEBUG, logger=billing.log.name):
        response = client_for(db).post("/api/billing/portal")
    assert response.status_code == 502
    assert response.json() == {"detail": billing._PORTAL_PROVIDER_FAILED}
    assert "LEAKED" not in response.text
    assert caplog.records
    assert not any("LEAKED" in record.getMessage() for record in caplog.records)


def test_portal_without_stripe_configured_fails_instead_of_faking_a_url(stripe, monkeypatch):
    monkeypatch.setattr(billing, "_stripe_available", False)
    response = client_for(FakeDB()).post("/api/billing/portal")
    assert response.status_code == 503
    assert "mock" not in response.text


# ── Log hygiene ───────────────────────────────────────────────────────────────

def test_billing_logs_are_constant_events():
    """No provider text, tenant or customer identifiers interpolated into logs."""
    source = (Path(__file__).resolve().parents[1] / "app/api/billing.py").read_text(encoding="utf-8")
    calls = [
        call for call in ast.walk(ast.parse(source))
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name) and call.func.value.id == "log"
    ]
    assert calls
    for call in calls:
        assert len(call.args) == 1 and not call.keywords, f"billing.py:{call.lineno} interpolates a log field"
        assert isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str), (
            f"billing.py:{call.lineno} must use a literal log event")


# ── Who may read billing history ──────────────────────────────────────────────

@pytest.mark.parametrize("role", ["manager", "back_office", "sales_rep", "viewer"])
@pytest.mark.parametrize("path", ["/api/billing/subscription", "/api/billing/invoices"])
def test_only_the_owner_can_read_billing_records(role, path):
    db = FakeDB(company_subscriptions=[{"stripe_customer_id": "cus_a"}])
    response = client_for(db, role=role).get(path)
    assert response.status_code == 403
    assert db.queries == []
