"""WhatsApp Business Messaging Policy — no provider calls or actual sends."""
from datetime import datetime, timedelta, timezone

from app.intelligence.whatsapp_message_policy import assess_whatsapp_outbound


NOW = datetime(2026, 11, 13, 22, tzinfo=timezone.utc)


def check(**overrides):
    args = dict(
        now=NOW,
        last_verified_customer_message_at=NOW - timedelta(hours=2),
        employee_approved=True,
        recipient_opted_in=True,
        recipient_opted_out=False,
        meta_policy_cleared=True,
        owner_verified=True,
        target_country="US",
        alcohol_promotional=False,
        recipient_age_verified=False,
        template_name=None,
        template_status=None,
        template_purpose_matches=False,
    )
    args.update(overrides)
    return assess_whatsapp_outbound(**args)


def test_recent_verified_inbound_enables_employee_approved_freeform_reply():
    d = check()
    assert d.permitted_for_employee_review and d.mode == "freeform"


def test_outbound_app_echo_does_not_open_customer_service_window():
    # Only a verified message from the *recipient* can supply this timestamp.
    d = check(last_verified_customer_message_at=None)
    assert not d.permitted_for_employee_review


def test_window_expires_at_exactly_24_hours():
    assert check(last_verified_customer_message_at=NOW - timedelta(hours=23, minutes=59)).mode == "freeform"
    assert check(last_verified_customer_message_at=NOW - timedelta(hours=24)).mode == "blocked"
    assert check(last_verified_customer_message_at=NOW - timedelta(hours=24),
                 template_name="follow_up", template_status="APPROVED",
                 template_purpose_matches=True).mode == "approved_template"


def test_template_draft_pending_or_wrong_purpose_rejected():
    for status in (None, "PENDING", "REJECTED", "PAUSED"):
        assert check(
            last_verified_customer_message_at=None,
            template_name="follow_up", template_status=status,
            template_purpose_matches=True,
        ).mode == "blocked"
    assert check(
        last_verified_customer_message_at=None,
        template_name="other_purpose", template_status="APPROVED",
        template_purpose_matches=False,
    ).mode == "blocked"


def test_employee_consent_and_recipient_opt_out_fail_closed():
    for kwargs in (
        {"employee_approved": False}, {"recipient_opted_in": False},
        {"recipient_opted_out": True}, {"owner_verified": False},
        {"meta_policy_cleared": False},
    ):
        assert check(**kwargs).mode == "blocked"


def test_us_alcohol_promotion_blocked_even_in_open_window_and_with_template():
    assert check(alcohol_promotional=True, recipient_age_verified=True).mode == "blocked"
    assert check(
        alcohol_promotional=True, recipient_age_verified=True,
        template_name="approved_followup", template_status="APPROVED",
    ).mode == "blocked"


def test_non_us_alcohol_requires_age_verification_and_other_policy_gates():
    assert check(target_country="DO", alcohol_promotional=True).mode == "blocked"
    assert check(target_country="DO", alcohol_promotional=True, recipient_age_verified=True).mode == "freeform"


def test_unknown_content_classification_and_unknown_jurisdiction_are_blocked():
    assert check(alcohol_promotional=None).mode == "blocked"
    assert check(target_country=None).mode == "blocked"
    assert check(target_country="").mode == "blocked"
    assert check(target_country="United States").mode == "blocked"


def test_future_timestamp_cannot_open_window():
    assert check(last_verified_customer_message_at=NOW + timedelta(hours=1)).mode == "blocked"


def test_naive_timestamp_is_rejected():
    assert check(now=datetime(2026, 11, 13, 22)).mode == "blocked"
