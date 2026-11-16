"""Employee-scoped, resumable metadata-only Gmail snapshot page staging.

A page is a bounded unit of **untrusted, non-actionable** snapshot evidence,
not a completed Gmail full sync. The initial getProfile historyId is held
encrypted from page zero until a SEPARATE final history reconciliation phase.
No background job, browser route, OAuth connection loader or follow-up action.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from app.intelligence.lola.incremental_replay import ReplayUnavailable, _history_id, _text
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, _filters,
)

_JOBS = "lola_gmail_snapshot_jobs"
_PAGES = "lola_gmail_snapshot_pages"
_RPC = "lola_stage_gmail_snapshot_page"
_MAX_ENTRIES = 100


@dataclass(frozen=True)
class SnapshotProgress:
    pages_staged: int
    messages_staged: int
    initial_history_id: str = field(repr=False)
    next_page_token: str | None = field(repr=False)
    initial_history_sha256: str
    next_page_sha256: str | None
    page_chain_sha256: str
    state: str
    # Staging does not imply complete source coverage or follow-up authority.
    history_reconciled: bool = False
    durable_provider_checkpoint: bool = False
    complete_mailbox_history: bool = False
    reply_absence_proven: bool = False
    follow_up_eligible: bool = False
    outbound_action_approved: bool = False


def _one(db: Any, table: str, fields: str, filters: Mapping[str, object]) -> dict | None:
    try:
        query = db.table(table).select(fields)
        for key, value in filters.items():
            query = query.eq(key, value)
        rows = query.limit(2).execute().data
    except Exception:
        raise CheckpointUnavailable("Scoped Gmail snapshot storage unavailable") from None
    if not isinstance(rows, list) or len(rows) > 1:
        raise CheckpointUnavailable("Ambiguous Gmail snapshot row")
    if not rows:
        return None
    row = rows[0]
    if (not isinstance(row, dict)
            or any(str(row.get(k)) != str(v) for k, v in filters.items())):
        raise CheckpointUnavailable("Gmail snapshot row outside employee scope")
    return row


def _anchor_digest(scope: MailboxScope, vault: CursorVault, history: str) -> str:
    return vault.digest(
        "gmail-snapshot-history-anchor-v1", scope.company_id,
        scope.employee_id, scope.provider_identity_ref, scope.resource_id, history,
    )


def _page_token_digest(scope: MailboxScope, vault: CursorVault, token: str) -> str:
    return vault.digest(
        "gmail-snapshot-next-page-v1", scope.company_id, scope.employee_id,
        scope.provider_identity_ref, scope.resource_id, token,
    )


def read_snapshot_progress(*, db: Any, scope: MailboxScope, vault: CursorVault
                           ) -> SnapshotProgress | None:
    filters = _filters(scope, vault)
    if scope.provider != "gmail":
        raise CheckpointUnavailable("Only Gmail supports snapshot staging")
    fields = (
        "company_id,employee_id,provider,provider_identity_sha256,resource_sha256,"
        "mapping_revision,initial_history_ciphertext,initial_history_sha256,"
        "next_page_ciphertext,next_page_sha256,page_chain_sha256,"
        "pages_staged,messages_staged,state"
    )
    row = _one(db, _JOBS, fields, filters)
    if row is None:
        return None
    if row.get("mapping_revision") != scope.mapping_revision:
        raise CheckpointUnavailable("Gmail snapshot mapping or consent has changed")
    pages, count = row.get("pages_staged"), row.get("messages_staged")
    if (type(pages) is not int or pages not in range(1, 501)
            or type(count) is not int or count not in range(0, 20001)
            or row.get("state") not in ("collecting", "snapshot_exhausted")):
        raise CheckpointUnavailable("Gmail snapshot ledger malformed")
    anchor = vault.decrypt(scope, row.get("initial_history_ciphertext"))
    _history_id(anchor)
    if row.get("initial_history_sha256") != _anchor_digest(scope, vault, anchor):
        raise CheckpointUnavailable("Gmail snapshot anchor does not match")
    token = None
    if row.get("state") == "collecting":
        token = vault.decrypt(scope, row.get("next_page_ciphertext"))
        _text(token, "snapshot page token")
        if row.get("next_page_sha256") != _page_token_digest(scope, vault, token):
            raise CheckpointUnavailable("Gmail snapshot page cursor mismatch")
    elif row.get("next_page_ciphertext") is not None or row.get("next_page_sha256") is not None:
        raise CheckpointUnavailable("Exhausted Gmail snapshot has continuation")
    chain = row.get("page_chain_sha256")
    if (not isinstance(chain, str) or len(chain) != 64
            or any(ch not in "0123456789abcdef" for ch in chain)):
        raise CheckpointUnavailable("Gmail snapshot chain digest malformed")
    return SnapshotProgress(
        pages_staged=pages, messages_staged=count,
        initial_history_id=anchor, next_page_token=token,
        initial_history_sha256=row["initial_history_sha256"],
        next_page_sha256=row.get("next_page_sha256"),
        page_chain_sha256=chain, state=row["state"],
    )


async def advance_gmail_snapshot_page(
    *,
    db: Any,
    scope: MailboxScope,
    vault: CursorVault,
    fetch_profile: Callable[[], Awaitable[Mapping[str, object]]],
    fetch_messages_page: Callable[[str | None], Awaitable[Mapping[str, object]]],
    fetch_message_metadata: Callable[[str], Awaitable[Mapping[str, object]]],
) -> SnapshotProgress:
    """Read, validate and atomically stage EXACTLY one bounded Gmail page.

    All fetch callbacks must be scoped to the same already-approved employee
    OAuth mailbox and reauthorize before/after each provider API call.
    The Stage 10 trigger performs a final authorization check at DB commit.
    """
    if scope.provider != "gmail" or not isinstance(vault, CursorVault):
        raise CheckpointUnavailable("Authenticated Gmail snapshot scope required")
    if not all(map(callable, (fetch_profile, fetch_messages_page, fetch_message_metadata))):
        raise CheckpointUnavailable("Missing authenticated Gmail snapshot fetchers")
    previous = read_snapshot_progress(db=db, scope=scope, vault=vault)
    if previous is not None and previous.state != "collecting":
        raise CheckpointUnavailable("Gmail snapshot already exhausted; reconciliation required")
    try:
        profile = await fetch_profile()
    except Exception:
        raise ReplayUnavailable("Gmail snapshot principal check unavailable") from None
    if not isinstance(profile, Mapping):
        raise ReplayUnavailable("Gmail snapshot profile malformed")
    current_id = _history_id(profile.get("historyId"))
    anchor = previous.initial_history_id if previous else current_id
    if int(current_id) < int(anchor):
        raise ReplayUnavailable("Gmail snapshot provider history regressed")
    cursor = previous.next_page_token if previous else None
    try:
        page = await fetch_messages_page(cursor)
    except Exception:
        raise ReplayUnavailable("Gmail snapshot listing unavailable") from None
    if not isinstance(page, Mapping):
        raise ReplayUnavailable("Gmail snapshot page malformed")
    rows = page.get("messages")
    if rows is None:
        if page.get("resultSizeEstimate") == 0 and page.get("nextPageToken") is None:
            rows = []
        else:
            raise ReplayUnavailable("Gmail snapshot page references missing")
    if not isinstance(rows, list) or len(rows) > _MAX_ENTRIES:
        raise ReplayUnavailable("Gmail snapshot page exceeded bounded entries")
    if previous and previous.messages_staged + len(rows) > 20000:
        raise ReplayUnavailable("Gmail snapshot requires approved larger mailbox plan")
    next_token = page.get("nextPageToken")
    if next_token is not None:
        next_token = _text(next_token, "Gmail snapshot page token")
        if not rows or next_token == cursor:
            raise ReplayUnavailable("Gmail snapshot continuation stalled")
    if not rows and (previous is None and page.get("resultSizeEstimate") != 0):
        raise ReplayUnavailable("Initial Gmail snapshot empty result unverified")
    unique: set[str] = set()
    entries: list[dict] = []
    for ref in rows:
        if not isinstance(ref, Mapping):
            raise ReplayUnavailable("Gmail snapshot ref malformed")
        msg_id = _text(ref.get("id"), "message ID", limit=256)
        listed_thread = ref.get("threadId")
        if listed_thread is not None:
            listed_thread = _text(listed_thread, "thread ID", limit=256)
        if msg_id in unique:
            raise ReplayUnavailable("Duplicate message in Gmail snapshot page")
        unique.add(msg_id)
        try:
            msg = await fetch_message_metadata(msg_id)
        except Exception:
            raise ReplayUnavailable("Gmail snapshot metadata unavailable") from None
        if not isinstance(msg, Mapping) or msg.get("id") != msg_id:
            raise ReplayUnavailable("Gmail snapshot metadata identity mismatch")
        real_thread = msg.get("threadId")
        if real_thread is not None:
            real_thread = _text(real_thread, "message thread ID", limit=256)
        if listed_thread is not None and listed_thread != real_thread:
            raise ReplayUnavailable("Gmail snapshot thread identity changed")
        history = _history_id(msg.get("historyId"))
        prefix = [scope.company_id, scope.employee_id, scope.provider_identity_ref, scope.resource_id]
        entries.append({
            "message_key_sha256": vault.digest("snapshot-message-v1", *prefix, msg_id),
            "thread_key_sha256": (
                vault.digest("snapshot-thread-v1", *prefix, real_thread)
                if real_thread else None
            ),
            "metadata_history_key_sha256": vault.digest("snapshot-history-v1", *prefix, history),
        })

    serialized = json.dumps(
        ["gmail-snapshot-page-v1", anchor, cursor, next_token, entries],
        separators=(",", ":"), ensure_ascii=True,
    )
    filters = _filters(scope, vault)
    digest = vault.digest("snapshot-page-digest-v1", serialized)
    args = {
        "p_company_id": scope.company_id,
        "p_employee_id": scope.employee_id,
        "p_provider_identity_sha256": filters["provider_identity_sha256"],
        "p_resource_sha256": filters["resource_sha256"],
        "p_mapping_revision": scope.mapping_revision,
        "p_expected_pages": previous.pages_staged if previous else 0,
        "p_expected_page_cursor_sha256": previous.next_page_sha256 if previous else None,
        "p_initial_history_ciphertext": vault.encrypt(scope, anchor),
        "p_initial_history_sha256": _anchor_digest(scope, vault, anchor),
        "p_next_page_ciphertext": vault.encrypt(scope, next_token) if next_token else None,
        "p_next_page_sha256": _page_token_digest(scope, vault, next_token) if next_token else None,
        "p_page_digest_sha256": digest,
        "p_entries": entries,
        "p_exhausted": next_token is None,
    }
    try:
        raw = db.rpc(_RPC, args).execute().data
        generation = raw[0] if isinstance(raw, list) and len(raw) == 1 else raw
    except Exception:
        raise CheckpointUnavailable("Atomic Gmail snapshot page commit failed") from None
    if type(generation) is not int or generation != args["p_expected_pages"] + 1:
        raise CheckpointUnavailable("Gmail snapshot page RPC generation mismatch")
    final = read_snapshot_progress(db=db, scope=scope, vault=vault)
    if (final is None or final.pages_staged != generation
            or final.messages_staged != (previous.messages_staged if previous else 0) + len(entries)
            or final.initial_history_id != anchor
            or final.next_page_token != next_token
            or final.state != ("snapshot_exhausted" if next_token is None else "collecting")):
        raise CheckpointUnavailable("Committed Gmail snapshot page did not verify")
    receipt = _one(db, _PAGES,
                   "company_id,employee_id,provider,provider_identity_sha256,"
                   "resource_sha256,page_index,page_digest_sha256,entry_count",
                   {**filters, "page_index": generation - 1})
    if (receipt is None or receipt.get("page_digest_sha256") != digest
            or receipt.get("entry_count") != len(entries)):
        raise CheckpointUnavailable("Committed Gmail snapshot receipt did not verify")
    return final
