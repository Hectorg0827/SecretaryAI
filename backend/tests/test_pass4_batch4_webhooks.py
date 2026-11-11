"""Signed, tenant-scoped and retryable webhook acceptance (synthetic only)."""
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api import webhooks
from app.main import app


COMPANY = "company-1"
OTHER = "company-2"


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.updates = db, table, {}, None

    def select(self, *_): return self
    def limit(self, *_): return self
    def eq(self, name, value):
        self.filters[name] = value
        return self
    def update(self, values):
        self.updates = values
        return self

    def execute(self):
        matches = [row for row in self.db.companies if all(
            row.get(key) == val for key, val in self.filters.items()
        )]
        if self.updates is not None:
            for row in matches:
                row.update(self.updates)
        return SimpleNamespace(data=[dict(row) for row in matches])


class FakeDB:
    def __init__(self):
        self.companies = [
            {"id": COMPANY, "qb_type": "online", "qbo_realm_id": "realm-1", "conductor_end_user_id": "conductor-1"},
            {"id": OTHER, "qb_type": "online", "qbo_realm_id": "realm-2", "conductor_end_user_id": "conductor-2"},
        ]

    def table(self, name):
        assert name == "companies"
        return FakeQuery(self, name)


class FakeRedis:
    def __init__(self): self.entries = {}

    def set(self, key, value, nx, ex):
        assert nx and ex == 60
        if key in self.entries:
            return False
        self.entries[key] = value
        return True

    def eval(self, _script, _key_count, key, marker):
        if self.entries.get(key) == marker:
            del self.entries[key]
            return 1
        return 0


@pytest.fixture
def harness(monkeypatch):
    db, redis, dispatched = FakeDB(), FakeRedis(), []
    monkeypatch.setattr(webhooks, "_db", lambda: db)
    monkeypatch.setattr(webhooks, "_redis", lambda: redis)
    monkeypatch.setattr(webhooks, "_dispatch", dispatched.append)
    monkeypatch.setattr(webhooks.settings, "intuit_webhook_verifier_token", "local-verifier")
    monkeypatch.setattr(webhooks.settings, "conductor_webhook_secret", "local-conductor-secret")
    return TestClient(app), db, redis, dispatched


def _qbo(client, realm, *, entity="Invoice", verifier="local-verifier"):
    raw = json.dumps({"eventNotifications": [{"realmId": realm, "dataChangeEvent": {
        "entities": [{"name": entity, "id": "synthetic"}],
    }}]}).encode()
    sig = base64.b64encode(hmac.new(verifier.encode(), raw, hashlib.sha256).digest()).decode()
    return client.post("/webhooks/qbo", content=raw,
                       headers={"intuit-signature": sig, "content-type": "application/json"})


def test_signed_qbo_targets_exact_company_and_coalesces(harness):
    client, _, redis, dispatched = harness
    assert _qbo(client, "realm-1").json()["refreshes_queued"] == 1
    assert _qbo(client, "realm-1").json()["refreshes_queued"] == 0
    assert _qbo(client, "realm-2").json()["refreshes_queued"] == 1
    assert dispatched == [COMPANY, OTHER]
    assert len(redis.entries) == 2


def test_qbo_wrong_signature_and_unknown_realm_cannot_dispatch(harness):
    client, _, _, dispatched = harness
    assert _qbo(client, "realm-1", verifier="wrong").status_code == 401
    assert _qbo(client, "not-onboarded").json()["refreshes_queued"] == 0
    assert _qbo(client, "realm-1", entity="Unrelated").json()["refreshes_queued"] == 0
    assert dispatched == []


def test_queue_failure_returns_retry_and_releases_claim(harness, monkeypatch):
    client, _, redis, dispatched = harness
    from fastapi import HTTPException
    monkeypatch.setattr(webhooks, "_dispatch", Mock(side_effect=HTTPException(503)))
    assert _qbo(client, "realm-1").status_code == 503
    assert not redis.entries
    monkeypatch.setattr(webhooks, "_dispatch", dispatched.append)
    assert _qbo(client, "realm-1").json()["refreshes_queued"] == 1
    assert dispatched == [COMPANY]


def test_duplicate_realm_mapping_fails_closed(harness):
    client, db, _, dispatched = harness
    db.companies[1]["qbo_realm_id"] = "realm-1"
    assert _qbo(client, "realm-1").json()["refreshes_queued"] == 0
    assert dispatched == []


def test_provider_events_ignore_stale_connection_types(harness):
    client, db, _, dispatched = harness
    db.companies[0]["qb_type"] = "desktop"
    assert _qbo(client, "realm-1").json()["refreshes_queued"] == 0
    headers = {"X-Conductor-Secret": "local-conductor-secret"}
    assert client.post("/webhooks/conductor", json={
        "endUserId": "conductor-2", "event": "sync.completed",
    }, headers=headers).json()["refreshes_queued"] == 0
    assert dispatched == []


def test_conductor_distinct_webhook_secret_and_failure_status(harness, monkeypatch):
    client, db, _, dispatched = harness
    db.companies[0]["qb_type"] = "desktop"
    from app.utils import audit
    audit_events = []
    monkeypatch.setattr(audit, "write_audit_event", lambda *_args, **kwargs:
                        audit_events.append(kwargs) or "event-1")
    url = "/webhooks/conductor"
    payload = {"endUserId": "conductor-1", "event": "sync.completed"}
    assert client.post(url, json=payload, headers={"X-Conductor-Secret": "api-key"}).status_code == 401
    headers = {"X-Conductor-Secret": "local-conductor-secret"}
    assert client.post(url, json=payload, headers=headers).json()["refreshes_queued"] == 1
    assert dispatched == [COMPANY]
    assert client.post(url, json={"endUserId": "conductor-1", "event": "sync.failed"},
                       headers=headers).status_code == 200
    assert db.companies[0]["qb_connection_status"] == "error"
    assert db.companies[1].get("qb_connection_status") is None
    assert audit_events[0]["company_id"] == COMPANY


def test_conductor_audit_outage_cannot_acknowledge_failure(harness, monkeypatch):
    client, db, _, _ = harness
    db.companies[0]["qb_type"] = "desktop"
    from app.utils import audit
    monkeypatch.setattr(audit, "write_audit_event", lambda *_a, **_k: None)
    response = client.post("/webhooks/conductor", json={"endUserId": "conductor-1", "event": "sync.failed"},
                           headers={"X-Conductor-Secret": "local-conductor-secret"})
    assert response.status_code == 503
    assert db.companies[0].get("qb_connection_status") is None


def test_refresh_task_name_is_registered_and_dispatches_company(monkeypatch):
    from tasks.dashboard_refresh import refresh_dashboard_snapshot
    from celery_app import app as celery
    assert webhooks.REFRESH_TASK in celery.tasks
    assert celery.tasks[webhooks.REFRESH_TASK].name == webhooks.REFRESH_TASK
    job = Mock(return_value=SimpleNamespace(id="synthetic-job"))
    monkeypatch.setattr(refresh_dashboard_snapshot, "apply_async", job)
    webhooks._dispatch(COMPANY)
    job.assert_called_once_with(kwargs={"company_id": COMPANY})
