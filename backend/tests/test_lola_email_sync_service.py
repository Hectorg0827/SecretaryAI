"""Zero-network authorized employee continuity-to-provider sync coordinator tests."""
from datetime import datetime, timezone
from dataclasses import replace

import httpx
import pytest

from app.intelligence.lola.continuity import WorkScope
from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase
from app.intelligence.lola import email_sync_service as svc
from app.intelligence.lola.replay_persistence import (
    CheckpointCommit, CheckpointUnavailable, CursorVault, StoredCheckpoint,
)
from app.intelligence.lola.incremental_replay import ReplayUnavailable

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-A/messages/delta"
GRAPH_FINAL = GRAPH_BASE + "?$deltatoken=verified-terminal"


def case(rev="r1"):
    return ResolvedCrossChannelCase(
        scope=WorkScope(COMPANY, EMPLOYEE, "CONTACT", "WORK", rev, True),
        email_provider_identity_ref="bound-employee-email-connection",
        whatsapp_provider_identity_ref="bound-employee-wa-connection",
        email_contact_ref="provider-contact-one",
        whatsapp_contact_ref="provider-wa-contact-one",
        source_fingerprint=rev,
    )


class FakeOutlook:
    async def _get_headers(self):
        return {"Authorization": "Bearer synthetic-not-real", "Content-Type": "application/json"}


class GmailShouldNotRead:
    def _get_service(self):
        pytest.fail("Cannot sync Gmail without verified full-sync baseline")


def call_args(**overrides):
    values = dict(
        user={"sub": EMPLOYEE, "company_id": COMPANY, "role": "sales_rep"},
        db=object(), contact_id="CONTACT", work_item_id="WORK",
        provider="outlook", verified_resource_id="FOLDER-A",
        verified_principal="graph-id-verified", connector=FakeOutlook(),
        vault=CursorVault(b"k" * 32), now=NOW,
    )
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_gmail_missing_checkpoint_runs_verified_full_sync_before_atomic_commit(monkeypatch):
    from app.intelligence.lola.gmail_bootstrap import GmailBootstrapEvidence
    from app.intelligence.lola.incremental_replay import ChangeRef
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: case())
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)

    async def evidence(**kwargs):
        return GmailBootstrapEvidence(
            next_history_id="1005",
            changed_records=(ChangeRef("PRIVATE-message", "added"),),
            page_count=2, message_count=1, history_record_count=0,
            digest="a" * 64,
        )
    monkeypatch.setattr(svc, "collect_gmail_bootstrap", evidence)
    committed = []
    def commit(**kwargs):
        committed.append(kwargs)
        assert isinstance(kwargs["replay"], GmailBootstrapEvidence)
        assert kwargs["previously_read"] is None
        return CheckpointCommit(1, "gmail", 1, "a"*64, NOW.isoformat())

    monkeypatch.setattr(svc, "commit_replay", commit)
    output = await svc.synchronize_verified_case_email(**call_args(
        provider="gmail", verified_resource_id="employee@private.invalid",
        verified_principal="employee@private.invalid", connector=GmailShouldNotRead(),
    ))
    assert output.state == "baseline_recorded"
    assert output.committed_generation == 1
    assert output.follow_up_eligible is False
    assert output.reply_status == "not_assessed"
    assert output.ingested_metadata_count == 1
    assert len(committed) == 1


@pytest.mark.asyncio
async def test_graph_bootstrap_only_reports_baseline_and_requires_atomic_commit(monkeypatch):
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: case())
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)
    calls = []
    def commit(**kwargs):
        calls.append(kwargs)
        assert kwargs["replay"].initial_baseline is True
        assert kwargs["replay"].next_delta_url == GRAPH_FINAL
        assert kwargs["previously_read"] is None
        return CheckpointCommit(1, "outlook", 1, "a" * 64, NOW.isoformat())

    monkeypatch.setattr(svc, "commit_replay", commit)

    def handler(request):
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "graph-id-verified"})
        assert request.url.path.endswith("/FOLDER-A/messages/delta")
        return httpx.Response(200, json={
            "value": [{"id": "private-email-provider-id"}],
            "@odata.deltaLink": GRAPH_FINAL,
        })
    output = await svc.synchronize_verified_case_email(**call_args(
        http_transport=httpx.MockTransport(handler),
    ))
    assert output.state == "baseline_recorded"
    assert output.committed_generation == 1
    assert output.ingested_metadata_count == 1
    assert output.cross_channel_case_ready is False
    assert output.follow_up_eligible is False
    assert output.external_messages_sent is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_revoked_provider_grant_on_reauthorization_prevents_checkpoint_commit(monkeypatch):
    calls = []
    def resolver(**kwargs):
        calls.append(True)
        return case("r1" if len(calls) <= 1 else "revoked-r2")
    monkeypatch.setattr(svc, "resolve_verified_case", resolver)
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)
    committed = []
    monkeypatch.setattr(svc, "commit_replay", lambda **kwargs: committed.append(True))
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"id": "graph-id-verified"})
    with pytest.raises(ReplayUnavailable):
        await svc.synchronize_verified_case_email(**call_args(
            http_transport=httpx.MockTransport(handler),
        ))
    assert not committed
    assert not requests


@pytest.mark.asyncio
async def test_unverified_employee_mapping_prevents_any_cursor_read(monkeypatch):
    calls = []
    def deny(**kwargs):
        raise PermissionError("not an authorized employee")
    monkeypatch.setattr(svc, "resolve_verified_case", deny)
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: calls.append("READ"))
    with pytest.raises(PermissionError):
        await svc.synchronize_verified_case_email(**call_args())
    assert calls == []


@pytest.mark.asyncio
async def test_rpc_failure_does_not_report_completed_ingestion(monkeypatch):
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: case())
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)
    def reject(**kwargs):
        raise CheckpointUnavailable("atomic checkpoint rolled back")
    monkeypatch.setattr(svc, "commit_replay", reject)

    def handler(request):
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "graph-id-verified"})
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": GRAPH_FINAL})
    with pytest.raises(CheckpointUnavailable):
        await svc.synchronize_verified_case_email(**call_args(
            http_transport=httpx.MockTransport(handler),
        ))


@pytest.mark.asyncio
async def test_graph_provider_410_never_advances_cursor_or_sends_messages(monkeypatch):
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: case())
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)
    committed = []
    monkeypatch.setattr(svc, "commit_replay", lambda **kwargs: committed.append(True))

    def handler(request):
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "graph-id-verified"})
        return httpx.Response(410, json={"error": {"message": "PRIVATE BODY"}})
    output = await svc.synchronize_verified_case_email(**call_args(
        http_transport=httpx.MockTransport(handler),
    ))
    assert output.state == "requires_graph_folder_resync"
    assert output.follow_up_eligible is False
    assert output.ingested_metadata_count == 0
    assert committed == []


@pytest.mark.asyncio
async def test_user_cannot_supply_unsupported_provider(monkeypatch):
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: pytest.fail("must not resolve"))
    with pytest.raises(CheckpointUnavailable):
        await svc.synchronize_verified_case_email(**call_args(provider="unknown-mail-provider"))



@pytest.mark.asyncio
async def test_gmail_initial_anchor_404_never_commits_partial_baseline(monkeypatch):
    from app.intelligence.lola.incremental_replay import GmailHistoryExpired
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: case())
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)
    async def expired(**kwargs):
        raise GmailHistoryExpired("Synthetic initial Gmail history expired")
    monkeypatch.setattr(svc, "collect_gmail_bootstrap", expired)
    calls = []
    monkeypatch.setattr(svc, "commit_replay", lambda **kwargs: calls.append(True))
    output = await svc.synchronize_verified_case_email(**call_args(
        provider="gmail", verified_resource_id="employee@private.invalid",
        verified_principal="employee@private.invalid", connector=GmailShouldNotRead(),
    ))
    assert output.state == "requires_full_gmail_bootstrap_retry"
    assert output.committed_generation is None
    assert output.follow_up_eligible is False
    assert calls == []


@pytest.mark.asyncio
async def test_gmail_bounded_bootstrap_failure_never_commits_cursor(monkeypatch):
    monkeypatch.setattr(svc, "resolve_verified_case", lambda **kwargs: case())
    monkeypatch.setattr(svc, "read_checkpoint", lambda **kwargs: None)
    async def invalid(**kwargs):
        raise ReplayUnavailable("Mailbox snapshot exceeds bounded limit")
    monkeypatch.setattr(svc, "collect_gmail_bootstrap", invalid)
    calls = []
    monkeypatch.setattr(svc, "commit_replay", lambda **kwargs: calls.append(True))
    with pytest.raises(ReplayUnavailable):
        await svc.synchronize_verified_case_email(**call_args(
            provider="gmail", verified_resource_id="employee@private.invalid",
            verified_principal="employee@private.invalid", connector=GmailShouldNotRead(),
        ))
    assert calls == []
