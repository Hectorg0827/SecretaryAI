"""Synthetic no-network tests of Lola cross-channel follow-up policy."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence.lola.continuity import (
    Activity, ActivityRecord, Channel, ChannelCoverage, ContinuityState,
    FollowUpPolicy, WorkScope, evaluate_follow_up,
)

NOW = datetime(2026, 11, 14, 18, tzinfo=timezone.utc)
SCOPE = WorkScope(
    company_id="tenant-A", employee_id="employee-A",
    contact_id="contact-J", work_item_id="label-project",
    mapping_revision="registry-rev-4", employee_consent_verified=True,
)
REQUEST = ActivityRecord(
    company_id=SCOPE.company_id, employee_id=SCOPE.employee_id,
    contact_id=SCOPE.contact_id, work_item_id=SCOPE.work_item_id,
    channel=Channel.EMAIL, activity=Activity.REQUEST_RECEIVED,
    source_record_id="gmail-44", source_revision="gmail-v1",
    observed_at=NOW - timedelta(days=3),
    source_authenticated=True, contact_mapping_verified=True,
    work_item_mapping_verified=True,
)
SENT = ActivityRecord(
    company_id=SCOPE.company_id, employee_id=SCOPE.employee_id,
    contact_id=SCOPE.contact_id, work_item_id=SCOPE.work_item_id,
    channel=Channel.WHATSAPP, activity=Activity.DRAFT_SENT,
    source_record_id="wa-echo-18", source_revision="wa-v1",
    observed_at=NOW - timedelta(days=2),
    source_authenticated=True, contact_mapping_verified=True,
    work_item_mapping_verified=True, outbound_delivery_confirmed=True,
)
REPLY = ActivityRecord(
    company_id=SCOPE.company_id, employee_id=SCOPE.employee_id,
    contact_id=SCOPE.contact_id, work_item_id=SCOPE.work_item_id,
    channel=Channel.WHATSAPP, activity=Activity.REPLY_RECEIVED,
    source_record_id="wa-in-19", source_revision="wa-in-v1",
    observed_at=NOW - timedelta(hours=20),
    source_authenticated=True, contact_mapping_verified=True,
    work_item_mapping_verified=True,
)


def channels(now=NOW):
    return [
        ChannelCoverage(
            company_id=SCOPE.company_id, employee_id=SCOPE.employee_id,
            channel=channel, window_start=now - timedelta(days=30),
            complete_through=now - timedelta(seconds=30),
            complete=True, source_revision=channel.value + "-cursor-3",
            connector_authorized=True,
        )
        for channel in (Channel.EMAIL, Channel.WHATSAPP)
    ]


def inspect(activities=None, coverage=None, scope=SCOPE, now=NOW):
    return evaluate_follow_up(
        scope, activities=[REQUEST, SENT] if activities is None else activities,
        coverage=channels(now) if coverage is None else coverage,
        now=now,
    )


def test_verified_email_request_and_whatsapp_draft_no_reply_review_only():
    result = inspect()
    assert result.state == ContinuityState.REVIEW_FOLLOW_UP
    assert result.requires_human_review is True
    assert result.cross_channel_link_verified is True
    assert result.supporting_record_ids == ("gmail-44", "wa-echo-18")
    assert len(result.evidence_fingerprint) == 64
    assert result.follow_up_after == (SENT.observed_at + timedelta(hours=24)).isoformat()
    assert result.messages_sent is False
    assert result.notifications_sent is False
    assert result.external_writes_executed is False


def test_verified_reply_after_sent_suppresses_follow_up():
    result = inspect(activities=[REQUEST, SENT, REPLY])
    assert result.state == ContinuityState.REPLIED
    assert result.requires_human_review is False
    assert "wa-in-19" in result.supporting_record_ids


def test_reply_on_email_also_suppresses_follow_up():
    result = inspect(activities=[REQUEST, SENT, replace(
        REPLY, channel=Channel.EMAIL, source_record_id="gmail-response")])
    assert result.state == ContinuityState.REPLIED


def test_pre_send_reply_does_not_count_as_draft_response():
    earlier = replace(REPLY, observed_at=SENT.observed_at - timedelta(hours=2))
    result = inspect(activities=[REQUEST, earlier, SENT])
    assert result.state == ContinuityState.REVIEW_FOLLOW_UP


def test_reply_for_different_verified_case_does_not_close_this_work_item():
    unrelated = replace(REPLY, work_item_id="another-label-project")
    result = inspect(activities=[REQUEST, SENT, unrelated])
    assert result.state == ContinuityState.REVIEW_FOLLOW_UP


def test_only_a_contact_name_similarity_is_never_a_link():
    other = replace(SENT, contact_id="other-person")
    result = inspect(activities=[REQUEST, other])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert result.evidence_fingerprint is None


def test_whatsapp_composer_draft_is_not_a_sent_message():
    result = inspect(activities=[REQUEST, replace(SENT, outbound_delivery_confirmed=False)])
    assert result.state == ContinuityState.NO_VERIFIED_WORKFLOW
    assert result.requires_human_review is False


def test_whatsapp_draft_sent_but_request_not_yet_received_is_unverifiable():
    result = inspect(activities=[replace(REQUEST, observed_at=SENT.observed_at + timedelta(hours=1)), SENT])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_before_waiting_period_is_not_due():
    now = SENT.observed_at + timedelta(hours=20)
    result = inspect(activities=[REQUEST, SENT], now=now)
    assert result.state == ContinuityState.NOT_DUE
    assert result.requires_human_review is False


def test_exact_waiting_threshold_is_due():
    now = SENT.observed_at + timedelta(hours=24)
    assert inspect(now=now).state == ContinuityState.REVIEW_FOLLOW_UP


@pytest.mark.parametrize("change", [
    {"employee_consent_verified": False},
    {"mapping_revision": ""},
    {"company_id": ""},
])
def test_unverified_scope_cannot_generate_reminder(change):
    result = inspect(scope=replace(SCOPE, **change))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert result.requires_human_review is False


def test_missing_whatsapp_coverage_fails_closed():
    result = inspect(coverage=channels()[:1])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_duplicate_channel_coverage_is_rejected():
    result = inspect(coverage=[channels()[0], channels()[0]])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_stale_complete_through_is_not_proof_of_silence():
    coverage = channels()
    coverage[1] = replace(coverage[1], complete_through=NOW - timedelta(hours=4))
    result = inspect(coverage=coverage)
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_partial_provider_sync_is_not_proof_of_silence():
    coverage = channels()
    coverage[0] = replace(coverage[0], complete=False)
    assert inspect(coverage=coverage).state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_whatsapp_employee_consent_is_required():
    coverage = channels()
    coverage[1] = replace(coverage[1], connector_authorized=False)
    assert inspect(coverage=coverage).state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_missing_historical_sync_interval_not_proof_of_no_reply():
    coverage = channels()
    coverage[1] = replace(coverage[1], window_start=SENT.observed_at - timedelta(hours=2))
    assert inspect(coverage=coverage).state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_wrong_tenant_activity_is_rejected():
    result = inspect(activities=[REQUEST, replace(SENT, company_id="tenant-B")])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_stale_employee_role_cannot_be_assumed_by_event():
    result = inspect(activities=[REQUEST, replace(SENT, employee_id="employee-B")])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_unverified_provider_event_is_not_used():
    result = inspect(activities=[REQUEST, replace(SENT, source_authenticated=False)])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_unverified_case_map_is_not_inferred():
    result = inspect(activities=[REQUEST, replace(SENT, work_item_mapping_verified=False)])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_conflicting_provider_message_revisions_fail_closed():
    result = inspect(activities=[REQUEST, SENT, replace(SENT, source_revision="another-revision")])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_same_delivered_webhook_repeated_is_idempotent():
    one = inspect(activities=[REQUEST, SENT])
    duplicate = inspect(activities=[REQUEST, SENT, SENT])
    assert one.state == duplicate.state == ContinuityState.REVIEW_FOLLOW_UP
    assert one.evidence_fingerprint == duplicate.evidence_fingerprint


def test_unverified_future_provider_event_fails_closed():
    result = inspect(activities=[REQUEST, replace(SENT, observed_at=NOW + timedelta(minutes=2))])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_naive_timestamp_is_rejected():
    result = inspect(activities=[REQUEST, replace(SENT, observed_at=NOW.replace(tzinfo=None))])
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_coverage_revision_changes_fingerprint():
    baseline = inspect()
    coverage = channels()
    coverage[0] = replace(coverage[0], source_revision="gmail-cursor-4")
    changed = inspect(coverage=coverage)
    assert baseline.state == changed.state == ContinuityState.REVIEW_FOLLOW_UP
    assert baseline.evidence_fingerprint != changed.evidence_fingerprint


def test_policy_configuration_is_bounded():
    with pytest.raises(ValueError):
        FollowUpPolicy(minimum_wait=timedelta(seconds=0))
    with pytest.raises(ValueError):
        FollowUpPolicy(max_records=0)


def test_oversized_event_batch_rejected_before_processing():
    result = inspect(activities=[REQUEST] * 501)
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
