"""Meta WhatsApp webhook signature and scoped source proof tests (no network)."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json

import pytest

from app.intelligence.lola.whatsapp_source_proof import (
    WebhookProofUnavailable, inspect_signed_whatsapp_webhook,
)

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
SECRET = b"synthetic-test-app-secret-long-enough"
COMPANY, EMPLOYEE = "tenant-A", "employee-A"
WABA, PHONE = "synthetic-waba-100", "synthetic-phone-number-id-200"


def payload():
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": WABA,
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": PHONE, "display_phone_number": "+15550009999"},
                    "contacts": [{"wa_id": "15550008888", "profile": {"name": "PRIVATE JOAN"}}],
                    "messages": [{
                        "id": "wamid.inbound-secret", "from": "15550008888",
                        "timestamp": str(int(NOW.timestamp())), "type": "text",
                        "text": {"body": "PRIVATE LABEL CONVERSATION"}
                    }],
                    "statuses": [{
                        "id": "wamid.outbound-secret", "status": "delivered",
                        "timestamp": str(int(NOW.timestamp())),
                        "recipient_id": "15550008888",
                    }]
                }
            }]
        }]
    }


def signed(value=None, *, secret=SECRET):
    raw = json.dumps(payload() if value is None else value, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return raw, signature


def check(value=None, *, signature=None, secret=SECRET, **kwargs):
    raw, valid = signed(value, secret=secret)
    return inspect_signed_whatsapp_webhook(
        raw_body=raw, signature_header=valid if signature is None else signature,
        meta_app_secret=secret, company_id=COMPANY, employee_id=EMPLOYEE,
        expected_waba_id=kwargs.pop("expected_waba_id", WABA),
        expected_phone_number_id=kwargs.pop("expected_phone_number_id", PHONE),
        received_at=kwargs.pop("received_at", NOW), **kwargs,
    )


def test_verified_signature_business_number_and_safe_metadata():
    receipt = check()
    assert receipt.signed_payload_verified is True
    assert receipt.phone_ownership_verified is True
    assert len(receipt.events) == 2
    assert receipt.events[0].event_kind == "inbound_observed"
    assert receipt.events[1].event_kind == "outbound_status_delivered"
    assert all(event.source_ref.startswith("wa-") and len(event.source_ref) == 35
               for event in receipt.events)
    assert receipt.webhook_checkpoint_persisted is False
    assert receipt.complete_message_history_verified is False
    assert receipt.reply_status == "not_assessed"
    assert receipt.outbound_follow_up_authorized is False
    assert receipt.external_writes_executed is False


def test_raw_payload_personal_content_never_in_output():
    receipt = check()
    text = str(receipt)
    for secret in ("PRIVATE", "15550008888", "15550009999",
                   "wamid.inbound-secret", "wamid.outbound-secret",
                   "synthetic-test-app-secret"):
        assert secret not in text
    assert len(receipt.source_revision) == 64


@pytest.mark.parametrize("attack", [
    "sha256=" + "0" * 64,
    "sha256=" + "f" * 63,
    "sha256=bad",
    "SHA256=" + "0" * 64,
    "",
    "sha256=" + "A" * 64,
    "sha256=" + "1" * 64 + "\nX-Header: leaked",
])
def test_invalid_webhook_signature_fails_closed(attack):
    with pytest.raises(WebhookProofUnavailable):
        check(signature=attack)


def test_signature_covers_exact_raw_bytes_even_if_same_json_data():
    raw, signature = signed()
    modified = raw + b" "
    with pytest.raises(WebhookProofUnavailable):
        inspect_signed_whatsapp_webhook(
            raw_body=modified, signature_header=signature,
            meta_app_secret=SECRET, company_id=COMPANY, employee_id=EMPLOYEE,
            expected_waba_id=WABA, expected_phone_number_id=PHONE,
            received_at=NOW,
        )


def test_wrong_waba_not_treated_as_our_business():
    with pytest.raises(WebhookProofUnavailable):
        check(expected_waba_id="other-business")


def test_wrong_phone_number_not_treated_as_employee_connection():
    with pytest.raises(WebhookProofUnavailable):
        check(expected_phone_number_id="other-phone")


@pytest.mark.parametrize("key,value", [
    ("object", "not_whatsapp"),
    ("entry", []),
    ("entry", "not-a-list"),
])
def test_malformed_top_level_events_fail_closed(key, value):
    bad = payload()
    bad[key] = value
    with pytest.raises(WebhookProofUnavailable):
        check(bad)


def test_untrusted_change_field_rejected_not_promoted_to_echo():
    bad = payload()
    bad["entry"][0]["changes"][0]["field"] = "smb_message_echoes"
    with pytest.raises(WebhookProofUnavailable):
        check(bad)


def test_inbound_message_from_missing_sender_not_accepted():
    bad = payload()
    del bad["entry"][0]["changes"][0]["value"]["messages"][0]["from"]
    with pytest.raises(WebhookProofUnavailable):
        check(bad)


def test_unknown_status_is_not_delivery_confirmation():
    bad = payload()
    bad["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = "unknown"
    with pytest.raises(WebhookProofUnavailable):
        check(bad)


def test_future_provider_timestamp_invalid():
    bad = payload()
    bad["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "2000000000"
    with pytest.raises(WebhookProofUnavailable):
        check(bad)


def test_invalid_received_at_time_invalid():
    with pytest.raises(WebhookProofUnavailable):
        check(received_at=datetime(2026, 11, 15, 12))


def test_status_does_not_prove_employee_sent_whatsapp_app_echo():
    result = check()
    assert result.events[1].employee_outbound_echo_verified is False
    assert result.events[1].linked_contact_verified is False
    assert result.events[0].complete_provider_history is False
    assert result.reply_status == "not_assessed"


def test_empty_valid_signed_change_does_not_establish_no_reply():
    bad = payload()
    bad["entry"][0]["changes"][0]["value"]["messages"] = []
    bad["entry"][0]["changes"][0]["value"]["statuses"] = []
    result = check(bad)
    assert result.events == ()
    assert result.complete_message_history_verified is False
    assert result.outbound_follow_up_authorized is False


def test_replay_same_signed_payload_produces_same_opaque_references_not_persistence():
    first = check()
    replay = check()
    assert [x.source_ref for x in first.events] == [x.source_ref for x in replay.events]
    assert replay.webhook_checkpoint_persisted is False


def test_two_events_with_same_identity_are_rejected():
    bad = payload()
    event = bad["entry"][0]["changes"][0]["value"]["messages"][0]
    bad["entry"][0]["changes"][0]["value"]["messages"].append(deepcopy(event))
    with pytest.raises(WebhookProofUnavailable):
        check(bad)


def test_valid_meta_signature_from_different_secret_is_invalid():
    raw, signature = signed(secret=b"another-long-secret-for-a-different-app")
    with pytest.raises(WebhookProofUnavailable):
        inspect_signed_whatsapp_webhook(
            raw_body=raw, signature_header=signature,
            meta_app_secret=SECRET, company_id=COMPANY, employee_id=EMPLOYEE,
            expected_waba_id=WABA, expected_phone_number_id=PHONE,
            received_at=NOW,
        )


def test_invalid_app_secret_is_not_permitted():
    with pytest.raises(WebhookProofUnavailable):
        check(secret=b"short")


def test_oversized_body_fails_before_parsing():
    payload_bytes = b"x" * 1_048_577
    signature = "sha256=" + hmac.new(SECRET, payload_bytes, hashlib.sha256).hexdigest()
    with pytest.raises(WebhookProofUnavailable):
        inspect_signed_whatsapp_webhook(
            raw_body=payload_bytes, signature_header=signature,
            meta_app_secret=SECRET, company_id=COMPANY, employee_id=EMPLOYEE,
            expected_waba_id=WABA, expected_phone_number_id=PHONE,
            received_at=NOW,
        )
