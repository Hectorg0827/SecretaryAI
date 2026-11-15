"""No-network Stage 5 proof boundary tests: registry + source snapshots."""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.intelligence.lola.continuity import (
    Activity, ActivityRecord, Channel, ChannelCoverage, ContinuityState,
)
from app.intelligence.lola.continuity_service import (
    VerifiedChannelSnapshot, evaluate_verified_case,
)
from app.intelligence.lola.identity_registry import IdentityAccessDenied
from test_lola_identity_registry import (
    COMPANY, CONTACT, EMPLOYEE, FakeDB, NOW, USER, WORK,
)

REQUEST = ActivityRecord(
    company_id=COMPANY, employee_id=EMPLOYEE, contact_id=CONTACT,
    work_item_id=WORK, channel=Channel.EMAIL, activity=Activity.REQUEST_RECEIVED,
    source_record_id="email:provider:request-1", source_revision="request-v1",
    observed_at=NOW - timedelta(days=3),
    source_authenticated=True, contact_mapping_verified=True,
    work_item_mapping_verified=True,
)
SENT = ActivityRecord(
    company_id=COMPANY, employee_id=EMPLOYEE, contact_id=CONTACT,
    work_item_id=WORK, channel=Channel.WHATSAPP, activity=Activity.DRAFT_SENT,
    source_record_id="whatsapp:provider:sent-1", source_revision="send-v1",
    observed_at=NOW - timedelta(days=2),
    source_authenticated=True, contact_mapping_verified=True,
    work_item_mapping_verified=True, outbound_delivery_confirmed=True,
)
REPLY = replace(
    SENT, activity=Activity.REPLY_RECEIVED, source_record_id="wa:reply:1",
    observed_at=NOW - timedelta(hours=2), outbound_delivery_confirmed=False,
)


def snapshots(records=(REQUEST, SENT), *, watermark=None):
    watermark = watermark or (NOW - timedelta(seconds=20))
    async def email_reader(resolved):
        return VerifiedChannelSnapshot(
            channel=Channel.EMAIL,
            provider_identity_ref=resolved.email_provider_identity_ref,
            mapping_revision=resolved.scope.mapping_revision,
            coverage=ChannelCoverage(
                company_id=COMPANY, employee_id=EMPLOYEE, channel=Channel.EMAIL,
                window_start=NOW - timedelta(days=30), complete_through=watermark,
                complete=True, source_revision="email-cursor:v7",
                connector_authorized=True,
            ),
            events=tuple(e for e in records if e.channel == Channel.EMAIL),
            source_kind="authorized_backend_provider",
            connector_identity_verified=True, pagination_complete=True,
            checkpoint_persisted=True,
        )
    async def whatsapp_reader(resolved):
        return VerifiedChannelSnapshot(
            channel=Channel.WHATSAPP,
            provider_identity_ref=resolved.whatsapp_provider_identity_ref,
            mapping_revision=resolved.scope.mapping_revision,
            coverage=ChannelCoverage(
                company_id=COMPANY, employee_id=EMPLOYEE, channel=Channel.WHATSAPP,
                window_start=NOW - timedelta(days=30), complete_through=watermark,
                complete=True, source_revision="wa-cursor:v19",
                connector_authorized=True,
            ),
            events=tuple(e for e in records if e.channel == Channel.WHATSAPP),
            source_kind="authorized_backend_provider",
            connector_identity_verified=True, pagination_complete=True,
            checkpoint_persisted=True,
        )
    return email_reader, whatsapp_reader


def check(db, *, readers=None, user=USER, timeout=6.0):
    email_reader, whatsapp_reader = readers or snapshots()
    return asyncio.run(evaluate_verified_case(
        user=user, db=db, contact_id=CONTACT, work_item_id=WORK,
        now=NOW, email_reader=email_reader, whatsapp_reader=whatsapp_reader,
        timeout_seconds=timeout,
    ))


def test_verified_employee_work_item_and_provider_snapshots_return_review_proposal():
    db = FakeDB()
    result = check(db)
    assert result.state == ContinuityState.REVIEW_FOLLOW_UP
    assert result.requires_human_review is True
    assert result.checked_through == (NOW - timedelta(seconds=20)).isoformat()
    assert result.notifications_sent is False
    assert result.external_writes_executed is False
    assert all(x[1] in ("select", "eq") for x in db.calls)


def test_verified_reply_closes_review_window():
    result = check(FakeDB(), readers=snapshots((REQUEST, SENT, REPLY)))
    assert result.state == ContinuityState.REPLIED
    assert result.requires_human_review is False


def test_missing_registry_migration_stops_provider_reads():
    db = FakeDB()
    db.failing_source = "lola_channel_authorizations"
    calls = []
    async def should_not_run(resolved):
        calls.append(1)
        raise AssertionError("Provider was invoked before identity verified")
    result = check(db, readers=(should_not_run, should_not_run))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert calls == []


def test_disabled_employee_denied_before_channel_reads():
    db = FakeDB()
    db.tables["users"][0]["is_active"] = False
    calls = []
    async def should_not_run(resolved):
        calls.append(1)
        raise AssertionError("Forbidden provider read")
    with pytest.raises(IdentityAccessDenied):
        check(db, readers=(should_not_run, should_not_run))
    assert calls == []


def test_revoked_employee_consent_never_calls_providers():
    db = FakeDB()
    db.tables["lola_channel_authorizations"][1]["employee_consent_revoked_at"] = NOW.isoformat()
    calls = []
    async def should_not_run(resolved):
        calls.append(1)
        raise AssertionError("Provider called without consent")
    result = check(db, readers=(should_not_run, should_not_run))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert calls == []


def test_swapped_whatsapp_connection_ref_rejected():
    email, whatsapp = snapshots()
    async def wrong_connection(resolved):
        original = await whatsapp(resolved)
        return replace(original, provider_identity_ref="another-user-phone-id")
    result = check(FakeDB(), readers=(email, wrong_connection))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_mismatch_provider_mapping_revision_rejected():
    email, whatsapp = snapshots()
    async def stale_mapping(resolved):
        return replace(await email(resolved), mapping_revision="stale-registry")
    result = check(FakeDB(), readers=(stale_mapping, whatsapp))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


@pytest.mark.parametrize("field,value", [
    ("pagination_complete", False),
    ("checkpoint_persisted", False),
    ("connector_identity_verified", False),
    ("source_kind", "browser_asserted"),
])
def test_unverified_provider_snapshot_is_not_proof_of_silence(field, value):
    email, whatsapp = snapshots()
    async def altered(resolved):
        return replace(await whatsapp(resolved), **{field: value})
    result = check(FakeDB(), readers=(email, altered))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert result.requires_human_review is False


def test_snapshot_from_wrong_tenant_rejected():
    email, whatsapp = snapshots()
    async def wrong_company(resolved):
        original = await whatsapp(resolved)
        return replace(original, coverage=replace(
            original.coverage, company_id="tenant-foreign"))
    result = check(FakeDB(), readers=(email, wrong_company))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_snapshot_contains_cross_tenant_event_rejected():
    bad = replace(SENT, company_id="tenant-foreign")
    result = check(FakeDB(), readers=snapshots((REQUEST, bad)))
    # The bad event's claimed channel is WhatsApp even though tenant mismatch.
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_duplicate_channel_snapshot_rejected():
    email, whatsapp = snapshots()
    async def spoofed(resolved):
        original = await whatsapp(resolved)
        return replace(original, channel=Channel.EMAIL)
    result = check(FakeDB(), readers=(email, spoofed))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_incomplete_second_source_never_infers_awaiting_reply():
    email, whatsapp = snapshots()
    async def missing_page(resolved):
        original = await whatsapp(resolved)
        return replace(original, coverage=replace(original.coverage, complete=False))
    result = check(FakeDB(), readers=(email, missing_page))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE


def test_reader_exception_sanitized():
    _, whatsapp = snapshots()
    async def failed(_resolved):
        raise RuntimeError("PRIVATE PROVIDER ACCESS TOKEN IN ERROR BODY")
    result = check(FakeDB(), readers=(failed, whatsapp))
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert "PRIVATE" not in result.reason


def test_reader_timeout_is_a_failed_coverage_not_a_due_reminder():
    _, whatsapp = snapshots()
    async def delayed(_resolved):
        await asyncio.sleep(.2)
        return None
    result = check(FakeDB(), readers=(delayed, whatsapp), timeout=.01)
    assert result.state == ContinuityState.EVIDENCE_UNAVAILABLE
    assert result.requires_human_review is False


def test_no_raw_provider_message_ids_in_output():
    result = check(FakeDB())
    assert result.supporting_record_ids
    assert all("provider" not in item for item in result.supporting_record_ids)
    assert all(item.startswith("source-") for item in result.supporting_record_ids)


def test_invalid_timeout_rejected_before_any_provider_call():
    with pytest.raises(ValueError):
        check(FakeDB(), timeout=0)
