"""Stage 12 Gmail recovery authorization and provider-404 orchestration tests."""
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence.lola.continuity import WorkScope
from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase
from app.intelligence.lola.incremental_replay import GmailHistoryExpired, ReplayUnavailable
from app.intelligence.lola.gmail_bootstrap import GmailBootstrapEvidence
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, StoredCheckpoint,
    _filters, scope_for_verified_case,
)
from app.intelligence.lola.replay_recovery import RecoveryCommit
from app.intelligence.lola import gmail_recovery_service as svc

COMPANY="11111111-1111-4111-8111-111111111111"
EMPLOYEE="22222222-2222-4222-8222-222222222222"
PRINCIPAL="employee@private.invalid"
V=CursorVault(b"k"*32)


def resolved(revision="rev1"):
    return ResolvedCrossChannelCase(
        scope=WorkScope(COMPANY,EMPLOYEE,"CONTACT","WORK",revision,True),
        email_provider_identity_ref="employee-own-mailbox-verified",
        whatsapp_provider_identity_ref="employee-own-whatsapp-verified",
        email_contact_ref="EMAIL-CONTACT",
        whatsapp_contact_ref="WA-CONTACT",
        source_fingerprint=revision,
    )


def checkpoint():
    return StoredCheckpoint(
        generation=3,plaintext_cursor="100",replay_digest="a"*64,
        baseline_only=False,replayed_through=datetime.now(timezone.utc)-timedelta(minutes=3),
        recovery_epoch=0,
    )


def approved(case, previous):
    scope=scope_for_verified_case(
        case, provider="gmail", resource_id=PRINCIPAL,
    )
    fields=_filters(scope,V)
    now=datetime.now(timezone.utc)
    return svc.VerifiedRecoveryApproval(
        company_id=COMPANY,employee_id=EMPLOYEE,
        provider_identity_sha256=fields["provider_identity_sha256"],
        resource_sha256=fields["resource_sha256"],
        mapping_revision=case.scope.mapping_revision,
        checkpoint_generation=previous.generation,
        approved_at=now-timedelta(minutes=1),
        expires_at=now+timedelta(minutes=10),
    )


def args(**kwargs):
    params=dict(
        user={"sub":EMPLOYEE,"company_id":COMPANY,"role":"sales_rep"},
        db=object(),contact_id="CONTACT",work_item_id="WORK",
        verified_resource_id=PRINCIPAL,verified_principal=PRINCIPAL,
        connector=object(),vault=V,approval_loader=approved,
    )
    params.update(kwargs)
    return params


def setup(monkeypatch):
    monkeypatch.setattr(svc,"resolve_verified_case",lambda **kwargs:resolved())
    monkeypatch.setattr(svc,"read_checkpoint",lambda **kwargs:checkpoint())


@pytest.mark.asyncio
async def test_actual_404_and_full_snapshot_required_before_approved_epoch_commit(monkeypatch):
    setup(monkeypatch)
    calls=[]
    async def expired(**kwargs):
        calls.append("old_404")
        assert kwargs["start_history_id"]=="100"
        raise GmailHistoryExpired("verified 404")
    async def baseline(**kwargs):
        calls.append("full_snapshot")
        return GmailBootstrapEvidence(
            next_history_id="250",changed_records=(),
            page_count=1,message_count=0,history_record_count=0,digest="b"*64,
        )
    def commit(**kwargs):
        calls.append("atomic_reset")
        assert isinstance(kwargs["observed_expiry"],GmailHistoryExpired)
        assert kwargs["baseline"].next_history_id=="250"
        assert kwargs["prior"].generation==3
        return RecoveryCommit(generation=4,recovery_epoch=1)
    monkeypatch.setattr(svc,"replay_gmail_history",expired)
    monkeypatch.setattr(svc,"collect_gmail_bootstrap",baseline)
    monkeypatch.setattr(svc,"commit_confirmed_gmail_recovery",commit)
    output=await svc.recover_verified_expired_gmail(**args(
        approval_loader=lambda case,previous: calls.append("approved") or approved(case,previous)
    ))
    assert calls==["approved","old_404","full_snapshot","approved","atomic_reset"]
    assert output.state=="audited_gmail_baseline_recovered"
    assert output.recovery_epoch==1
    assert output.committed_generation==4
    assert output.reply_status=="not_assessed"
    assert output.follow_up_eligible is False
    assert output.external_messages_sent is False


@pytest.mark.asyncio
async def test_current_old_cursor_requires_no_reset_even_if_approval_valid(monkeypatch):
    setup(monkeypatch)
    calls=[]
    async def valid(**kwargs):
        calls.append("old_cursor_valid")
        return object()
    monkeypatch.setattr(svc,"replay_gmail_history",valid)
    monkeypatch.setattr(svc,"collect_gmail_bootstrap",
                        lambda **kwargs: pytest.fail("Must not rebuild valid cursor"))
    monkeypatch.setattr(svc,"commit_confirmed_gmail_recovery",
                        lambda **kwargs: pytest.fail("Must not reset valid cursor"))
    result=await svc.recover_verified_expired_gmail(**args())
    assert result.state=="existing_gmail_history_still_valid"
    assert calls==["old_cursor_valid"]


@pytest.mark.asyncio
async def test_no_checkpoint_or_approval_blocks_provider_access(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(svc,"read_checkpoint",lambda **kwargs:None)
    monkeypatch.setattr(svc,"replay_gmail_history",
                        lambda **kwargs: pytest.fail("No sync without checkpoint"))
    result=await svc.recover_verified_expired_gmail(**args())
    assert result.state=="requires_initial_gmail_baseline"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["missing","expired","wrong_employee","wrong_generation","wrong_source"])
async def test_missing_or_untrusted_approval_prevents_any_provider_request(monkeypatch,kind):
    setup(monkeypatch)
    def loader(case,prior):
        from dataclasses import replace
        valid=approved(case,prior)
        if kind=="expired":
            return replace(valid,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        if kind=="wrong_employee":
            return replace(valid,employee_id="33333333-3333-4333-8333-333333333333")
        if kind=="wrong_generation":
            return replace(valid,checkpoint_generation=100)
        if kind=="wrong_source":
            return replace(valid,source="browser_approved")
        return None
    monkeypatch.setattr(svc,"replay_gmail_history",
                        lambda **kwargs: pytest.fail("Untrusted approval cannot read Gmail"))
    with pytest.raises(CheckpointUnavailable):
        await svc.recover_verified_expired_gmail(**args(approval_loader=loader))


@pytest.mark.asyncio
async def test_new_baseline_history_expires_before_commit(monkeypatch):
    setup(monkeypatch)
    calls=[]
    async def old(**kwargs):
        raise GmailHistoryExpired("verified old cursor expired")
    async def fresh(**kwargs):
        raise GmailHistoryExpired("fresh anchor expired during listing")
    monkeypatch.setattr(svc,"replay_gmail_history",old)
    monkeypatch.setattr(svc,"collect_gmail_bootstrap",fresh)
    monkeypatch.setattr(svc,"commit_confirmed_gmail_recovery",
                        lambda **kwargs: calls.append(True))
    result=await svc.recover_verified_expired_gmail(**args())
    assert result.state=="fresh_gmail_history_also_expired"
    assert not calls


@pytest.mark.asyncio
async def test_revocation_during_recovery_prevents_database_write(monkeypatch):
    calls=[]
    def resolver(**kwargs):
        calls.append("resolve")
        return resolved("rev1" if len(calls)<=1 else "revoked-r2")
    monkeypatch.setattr(svc,"resolve_verified_case",resolver)
    monkeypatch.setattr(svc,"read_checkpoint",lambda **kwargs:checkpoint())
    async def expired(**kwargs):
        raise GmailHistoryExpired("verified old cursor expired")
    async def snapshot(**kwargs):
        return GmailBootstrapEvidence(next_history_id="250",changed_records=(),
                                      page_count=1,message_count=0,
                                      history_record_count=0,digest="b"*64)
    monkeypatch.setattr(svc,"replay_gmail_history",expired)
    monkeypatch.setattr(svc,"collect_gmail_bootstrap",snapshot)
    monkeypatch.setattr(svc,"commit_confirmed_gmail_recovery",
                        lambda **kwargs: pytest.fail("Revoked consent cannot write"))
    with pytest.raises(CheckpointUnavailable):
        await svc.recover_verified_expired_gmail(**args())


@pytest.mark.asyncio
async def test_failed_recovery_rpc_never_returns_success(monkeypatch):
    setup(monkeypatch)
    async def expired(**kwargs):
        raise GmailHistoryExpired("verified expired old cursor")
    async def baseline(**kwargs):
        return GmailBootstrapEvidence(next_history_id="250",changed_records=(),
                                      page_count=1,message_count=0,
                                      history_record_count=0,digest="b"*64)
    def fail(**kwargs):
        raise CheckpointUnavailable("CAS conflict")
    monkeypatch.setattr(svc,"replay_gmail_history",expired)
    monkeypatch.setattr(svc,"collect_gmail_bootstrap",baseline)
    monkeypatch.setattr(svc,"commit_confirmed_gmail_recovery",fail)
    with pytest.raises(CheckpointUnavailable):
        await svc.recover_verified_expired_gmail(**args())
