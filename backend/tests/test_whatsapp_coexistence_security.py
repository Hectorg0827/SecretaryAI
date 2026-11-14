"""WhatsApp Coexistence boundary tests — synthetic Meta payloads, no API calls."""
import hashlib
import hmac
import json

from app.intelligence.whatsapp_coexistence import (
    resolve_signed_event_routes, verify_meta_signature,
    validated_whatsapp_routing_candidates,
)


def _payload(phone_id="phone-A", field="smb_message_echoes"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-A", "changes": [{
            "field": field,
            "value": {
                "metadata": {"phone_number_id": phone_id},
                "messages": [{"id": "message-1", "text": {
                    "body": "Confidential label draft for Joan"}},
                    {"id": "message-2", "text": {"body": "Another private note"}}],
            },
        }]}],
    }


def _owner(phone="phone-A", employee="employee-A", status="active"):
    return {
        "phone_number_id": phone, "company_id": "company-A", "user_id": employee,
        "meta_verified_at": "2026-11-13T15:00:00Z",
        "employee_consented_at": "2026-11-13T15:05:00Z",
        "policy_clearance_id": "test-policy-only",
        "status": status,
    }


def test_meta_hmac_requires_exact_raw_bytes_and_secret():
    body = json.dumps(_payload()).encode()
    secret = "unit-test-secret"
    signature = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    assert verify_meta_signature(body, signature, secret)
    assert not verify_meta_signature(body + b" ", signature, secret)
    assert not verify_meta_signature(body, signature, "wrong-key")
    assert not verify_meta_signature(body, None, secret)
    assert not verify_meta_signature(body, "sha256=" + "x" * 64, secret)
    assert not verify_meta_signature(body, "sha1=bad", secret)


def test_employee_routing_only_after_verified_signature_and_ownership():
    owners = {"phone-A": [_owner()]}
    assert resolve_signed_event_routes(
        _payload(), phone_owners=owners, signature_verified=False
    ) == []
    routed = resolve_signed_event_routes(
        _payload(), phone_owners=owners, signature_verified=True
    )
    assert len(routed) == 1
    assert routed[0].company_id == "company-A"
    assert routed[0].user_id == "employee-A"
    assert routed[0].field == "smb_message_echoes"
    assert routed[0].event_count == 2
    assert "Confidential" not in repr(routed)
    assert "Joan" not in repr(routed)


def test_duplicate_company_owner_or_unconsented_phone_is_not_routed():
    assert resolve_signed_event_routes(
        _payload(), phone_owners={"phone-A": [_owner(employee="A"), _owner(employee="B")]},
        signature_verified=True,
    ) == []
    without_consent = _owner()
    without_consent.pop("employee_consented_at")
    assert resolve_signed_event_routes(
        _payload(), phone_owners={"phone-A": [without_consent]}, signature_verified=True
    ) == []
    assert resolve_signed_event_routes(
        _payload(), phone_owners={"phone-A": [_owner(status="disconnected")]},
        signature_verified=True,
    ) == []


def test_unlinked_or_wrong_phone_number_cannot_fall_back_to_waba_owner():
    payload = _payload(phone_id="foreign-employee-phone")
    assert resolve_signed_event_routes(
        payload, phone_owners={"phone-A": [_owner()]}, signature_verified=True
    ) == []
    payload["entry"][0]["changes"][0]["value"]["metadata"] = {}
    assert resolve_signed_event_routes(
        payload, phone_owners={"phone-A": [_owner()]}, signature_verified=True
    ) == []


def test_history_and_contact_sync_are_excluded_until_separate_consent():
    for field in ("history", "smb_app_state_sync", "account_update"):
        assert resolve_signed_event_routes(
            _payload(field=field), phone_owners={"phone-A": [_owner()]},
            signature_verified=True,
        ) == []


def test_unknown_payload_rejected_instead_of_tenant_broadcast():
    assert resolve_signed_event_routes(
        {"object": "whatsapp_business_account", "entry": "invalid"},
        phone_owners={"phone-A": [_owner()]}, signature_verified=True
    ) == []
    assert resolve_signed_event_routes(
        {"object": "other", "entry": _payload()["entry"]},
        phone_owners={"phone-A": [_owner()]}, signature_verified=True
    ) == []



def test_routing_gate_never_accepts_unsigned_or_unapproved_pilot_payloads():
    raw_body = json.dumps(_payload()).encode()
    secret = "fixture-meta-app-secret"
    signature = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    base = {
        "raw_body": raw_body, "signature": signature, "app_secret": secret,
        "phone_owners": {"phone-A": [_owner()]},
    }
    assert validated_whatsapp_routing_candidates(**base) == []
    assert validated_whatsapp_routing_candidates(
        **base, staging_pilot_enabled=True
    ) == []
    assert validated_whatsapp_routing_candidates(
        **base, written_meta_policy_clearance=True
    ) == []
    assert validated_whatsapp_routing_candidates(
        **{**base, "signature": "sha256=" + "0" * 64},
        staging_pilot_enabled=True, written_meta_policy_clearance=True
    ) == []
    routed = validated_whatsapp_routing_candidates(
        **base, staging_pilot_enabled=True,
        written_meta_policy_clearance=True,
    )
    assert len(routed) == 1
    assert routed[0].user_id == "employee-A"
    assert "Confidential" not in repr(routed)


def test_invalid_json_and_excessive_webhook_size_are_rejected():
    assert validated_whatsapp_routing_candidates(
        raw_body=b"{bad", signature=None, app_secret="secret",
        phone_owners={}, staging_pilot_enabled=True,
        written_meta_policy_clearance=True,
    ) == []
    assert validated_whatsapp_routing_candidates(
        raw_body=b"x" * (256 * 1024 + 1), signature=None, app_secret="secret",
        phone_owners={}, staging_pilot_enabled=True,
        written_meta_policy_clearance=True,
    ) == []
