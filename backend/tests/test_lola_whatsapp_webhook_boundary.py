"""No WhatsApp chats can enter Lola via the staging Meta callback boundary."""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import lola_whatsapp_webhooks as webhook


def _config(**overrides):
    d = {
        "secretaryai_deployment_env": "staging",
        "lola_private_intelligence_enabled": True,
        "lola_meta_webhooks_enabled": True,
        "lola_meta_app_review_approved": True,
        "lola_meta_ai_policy_approved": True,
        "lola_meta_waba_privacy_approved": True,
        "lola_meta_policy_clearance_reference": "unit-test-only",
        "lola_meta_app_secret": "test-meta-signature-secret",
        "lola_meta_webhook_verify_token": "test-meta-verification-token",
    }
    d.update(overrides)
    return SimpleNamespace(**d)


class Request:
    def __init__(self, body: bytes, headers=None):
        self._raw = body
        self.headers = headers or {}

    async def body(self):
        return self._raw


class Query:
    def __init__(self, db):
        self.db = db

    def select(self, *args):
        return self

    def in_(self, *args):
        return self

    def eq(self, *args):
        return self

    def execute(self):
        self.db.reads += 1
        return SimpleNamespace(data=[])


class DB:
    def __init__(self):
        self.reads = 0
        self.writes = 0

    def table(self, name):
        assert name == "lola_employee_whatsapp_connections"
        return Query(self)


def _raw():
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "1234567890", "changes": [
            {"field": "smb_message_echoes", "value": {
                "metadata": {"phone_number_id": "9999999999"},
                "message_echoes": [{"id": "wamid.private",
                                  "text": {"body": "Private draft label for Joan"}}],
            }},
        ]}],
    }).encode()


def _signature(body: bytes, secret: str = "test-meta-signature-secret"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_meta_get_challenge_disabled_outside_staging(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config(
        secretaryai_deployment_env="production"))
    with pytest.raises(HTTPException) as exc:
        webhook.verify_callback(
            mode="subscribe", verify_token="test-meta-verification-token",
            challenge="12345",
        )
    assert exc.value.status_code == 404


def test_meta_get_challenge_requires_nonempty_secret(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config(
        lola_meta_webhook_verify_token=""))
    with pytest.raises(HTTPException) as exc:
        webhook.verify_callback(mode="subscribe", verify_token="", challenge="challenge")
    assert exc.value.status_code == 404


def test_meta_get_challenge_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config())
    with pytest.raises(HTTPException) as exc:
        webhook.verify_callback(
            mode="subscribe", verify_token="bad-token", challenge="12345",
        )
    assert exc.value.status_code == 403
    response = webhook.verify_callback(
        mode="subscribe", verify_token="test-meta-verification-token",
        challenge="12345",
    )
    assert response.body == b"12345"


@pytest.mark.asyncio
async def test_signed_test_payload_acknowledged_but_never_stored(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config())
    raw = _raw()
    db = DB()
    result = await webhook.receive_callback(
        Request(raw, {"x-hub-signature-256": _signature(raw)}), db=db
    )
    assert result == {
        "acknowledged": True, "messages_processed": 0, "messages_stored": 0
    }
    assert db.reads == 1
    assert db.writes == 0
    assert "Joan" not in repr(result)


@pytest.mark.asyncio
async def test_webhook_signature_tampering_is_rejected_before_db_lookup(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config())
    db = DB()
    with pytest.raises(HTTPException) as exc:
        await webhook.receive_callback(
            Request(_raw(), {"x-hub-signature-256": "sha256=" + "0" * 64}), db=db
        )
    assert exc.value.status_code == 403
    assert db.reads == 0


@pytest.mark.asyncio
async def test_webhook_disabled_by_policy_gate_even_if_signed(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config(
        lola_meta_ai_policy_approved=False))
    raw = _raw()
    with pytest.raises(HTTPException) as exc:
        await webhook.receive_callback(
            Request(raw, {"x-hub-signature-256": _signature(raw)}), db=DB()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unsigned_giant_payload_rejected_without_processing(monkeypatch):
    monkeypatch.setattr(webhook, "get_settings", lambda: _config())
    with pytest.raises(HTTPException) as exc:
        await webhook.receive_callback(
            Request(b"x" * (256 * 1024 + 1), {
                "content-length": str(256 * 1024 + 1),
            }),
            db=DB(),
        )
    assert exc.value.status_code == 413
