"""Signed synthetic webhook regression tests; no provider calls or real keys."""
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import billing


@pytest.fixture
def webhook(monkeypatch):
    app = FastAPI()
    app.include_router(billing.router, prefix="/api/billing")
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"duplicate": False, "applied": True}
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"company_id": "00000000-0000-4000-8000-000000000001"},
    ]
    app.dependency_overrides[billing.get_db] = lambda: db
    monkeypatch.setattr(billing, "_stripe_available", True)
    monkeypatch.setattr(billing.settings, "stripe_webhook_secret", "whsec_synthetic")
    monkeypatch.setattr(billing.settings, "secretaryai_deployment_env", "staging")
    monkeypatch.setattr(billing._stripe.Subscription, "retrieve", MagicMock(return_value={
        "id": "sub_synthetic", "customer": "cus_synthetic", "status": "active",
        "items": {"data": [{"current_period_start": 1700000000,
                            "current_period_end": 1702592000}]},
    }))
    return TestClient(app), db


def send(client, event, secret="whsec_synthetic"):
    payload = json.dumps(event)
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    return client.post("/api/billing/webhook", content=payload, headers={
        "Stripe-Signature": f"t={timestamp},v1={signature}",
        "Content-Type": "application/json",
    })


def event(kind="customer.subscription.updated", **changes):
    result = {"id": "evt_synthetic", "object": "event", "type": kind,
              "livemode": False, "api_version": "2026-09-30.endive",
              "data": {"object": {"id": "sub_synthetic", "customer": "cus_synthetic",
                                   "status": "active", "items": {"data": [{
                                       "current_period_start": 1700000000,
                                       "current_period_end": 1702592000,
                                   }]}}}}
    result.update(changes)
    return result


def test_signed_endive_subscription_updates_periods(webhook, monkeypatch):
    client, db = webhook
    assert send(client, event()).status_code == 200
    name, update = db.rpc.call_args.args
    assert name == "billing_apply_stripe_event"
    assert update["p_period_start"] == "2023-11-14T22:13:20+00:00"
    assert update["p_period_end"] == "2023-12-14T22:13:20+00:00"
    assert update["p_status"] == "active"
    assert update["p_company_id"] == "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("livemode", [True, None])
def test_staging_rejects_live_or_unspecified_mode(webhook, livemode):
    client, db = webhook
    assert send(client, event(livemode=livemode)).status_code == 400
    db.table.assert_not_called()


def test_bad_signature_rejected_without_database_writes(webhook):
    client, db = webhook
    assert send(client, event(), secret="wrong").status_code == 400
    db.table.assert_not_called()


def test_processing_failure_is_retried(webhook, monkeypatch):
    client, db = webhook
    db.rpc.return_value.execute.side_effect = RuntimeError("unavailable")
    response = send(client, event())
    assert response.status_code == 500
    assert response.json() == {"detail": "Webhook processing failed"}


def test_acacia_subscription_periods_still_supported():
    assert billing._subscription_period({"current_period_start": 1700000000, "current_period_end": 1702592000}) == (
        "2023-11-14T22:13:20+00:00", "2023-12-14T22:13:20+00:00")


def test_mixed_interval_periods_are_not_guessed():
    with pytest.raises(ValueError):
        billing._subscription_period({"items": {"data": [{}, {}]}})


@pytest.mark.parametrize("invoice", [
    {"subscription": "sub_synthetic"},
    {"parent": {"type": "subscription_details", "subscription_details": {"subscription": "sub_synthetic"}}},
    {"parent": {"type": "subscription_details", "subscription_details": {"subscription": {"id": "sub_synthetic"}}}},
])
def test_invoice_subscription_shapes(invoice):
    assert billing._invoice_subscription_id(invoice) == "sub_synthetic"


def test_invoice_parent_discriminator_checked():
    assert billing._invoice_subscription_id({"parent": {"type": "quote_details", "subscription_details": {"subscription": "sub_wrong"}}}) is None


def test_replayed_webhook_is_processed_through_atomic_ledger(webhook):
    client, db = webhook
    assert send(client, event()).status_code == 200
    db.rpc.return_value.execute.return_value.data = {"duplicate": True, "applied": False}
    assert send(client, event()).status_code == 200
    assert db.rpc.call_count == 2
    assert all(c.args[0] == "billing_apply_stripe_event" for c in db.rpc.call_args_list)


def test_out_of_order_failure_reconciles_current_stripe_status(webhook):
    client, db = webhook
    failing_invoice = event("invoice.payment_failed")
    failing_invoice["data"]["object"] = {
        "id": "in_old", "customer": "cus_synthetic", "subscription": "sub_synthetic",
        "amount_due": 24900,
    }
    # Stripe's *current* subscription is active, even though the old invoice
    # failure is being retried. Never blindly write past_due from the event.
    assert send(client, failing_invoice).status_code == 200
    assert db.rpc.call_args.args[1]["p_status"] == "active"
    assert db.rpc.call_args.args[1]["p_amount_usd"] == 249


def test_paid_invoice_passes_business_object_id_for_atomic_deduplication(webhook):
    client, db = webhook
    paid_invoice = event("invoice.paid")
    paid_invoice["data"]["object"] = {
        "id": "in_unique", "customer": "cus_synthetic", "subscription": "sub_synthetic",
        "amount_paid": 24900,
    }
    assert send(client, paid_invoice).status_code == 200
    assert db.rpc.call_args.args[1]["p_object_id"] == "in_unique"


def test_checkout_session_passes_business_object_id_for_atomic_deduplication(webhook):
    client, db = webhook
    completed = event("checkout.session.completed")
    completed["data"]["object"] = {
        "id": "cs_unique", "customer": "cus_synthetic", "subscription": "sub_synthetic",
        "metadata": {"company_id": "00000000-0000-4000-8000-000000000001",
                     "plan_id": "growth", "checkout_attempt_id": "00000000-0000-4000-8000-000000000002"},
    }
    assert send(client, completed).status_code == 200
    params = db.rpc.call_args.args[1]
    assert params["p_object_id"] == params["p_checkout_session_id"] == "cs_unique"
    assert params["p_checkout_attempt_id"] == "00000000-0000-4000-8000-000000000002"


def test_missing_invoice_id_is_retried_not_silently_ledgered(webhook):
    client, db = webhook
    paid_invoice = event("invoice.paid")
    paid_invoice["data"]["object"] = {
        "customer": "cus_synthetic", "subscription": "sub_synthetic",
        "amount_paid": 24900,
    }
    assert send(client, paid_invoice).status_code == 500
    db.rpc.assert_not_called()


def test_invoice_that_arrives_before_checkout_requests_retry(webhook):
    client, db = webhook
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    invoice = event("invoice.paid")
    invoice["data"]["object"] = {
        "id": "in_early", "customer": "cus_synthetic", "subscription": "sub_synthetic",
    }
    assert send(client, invoice).status_code == 500
    db.rpc.assert_not_called()


def test_webhook_rejects_inconsistent_stripe_subscription_identity(webhook):
    client, db = webhook
    billing._stripe.Subscription.retrieve.return_value["customer"] = "cus_other"
    assert send(client, event()).status_code == 500
    db.rpc.assert_not_called()
