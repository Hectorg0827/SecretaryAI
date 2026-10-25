"""Signed synthetic webhook regression tests; no provider calls or real keys."""
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import billing


@pytest.fixture
def webhook(monkeypatch):
    app = FastAPI()
    app.include_router(billing.router, prefix="/api/billing")
    db = MagicMock()
    app.dependency_overrides[billing.get_db] = lambda: db
    monkeypatch.setattr(billing, "_stripe_available", True)
    monkeypatch.setattr(billing.settings, "stripe_webhook_secret", "whsec_synthetic")
    monkeypatch.setattr(billing.settings, "secretaryai_deployment_env", "staging")
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
    monkeypatch.setattr(billing, "_company_id_from_customer", lambda *args: "company-synthetic")
    assert send(client, event()).status_code == 200
    update = db.table.return_value.update.call_args.args[0]
    assert update["current_period_start"] == "2023-11-14T22:13:20+00:00"
    assert update["current_period_end"] == "2023-12-14T22:13:20+00:00"


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
    client, _ = webhook
    monkeypatch.setattr(billing, "_handle_subscription_updated", AsyncMock(side_effect=RuntimeError("unavailable")))
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


def test_event_storage_failure_is_not_silenced():
    db = MagicMock()
    db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        billing._record_billing_event(db, "company", "invoice.paid", 1, "evt", {})


def test_duplicate_event_violations_are_safe_to_retry():
    db = MagicMock()
    error = RuntimeError("duplicate")
    error.code = "23505"
    db.table.return_value.insert.return_value.execute.side_effect = error
    billing._record_billing_event(db, "company", "invoice.paid", 1, "evt", {})
