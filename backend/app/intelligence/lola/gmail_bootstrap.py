"""Bounded Gmail full-sync bootstrap with concurrent-history reconciliation.

No OAuth secrets, database writes, network transport creation or user routes.
All four injected fetchers must be server-authenticated employee-scoped reads.
A complete initial snapshot + history replay can seed a future incremental
cursor; it CANNOT establish complete historical correspondence or "no reply".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from app.intelligence.lola.incremental_replay import (
    ChangeRef, GmailHistoryExpired, ReplayUnavailable,
    _history_id, _text, replay_gmail_history,
)

MAX_SNAPSHOT_PAGES = 100
MAX_SNAPSHOT_MESSAGES = 400
MAX_INITIAL_EVENTS = 500

ProfileFetch = Callable[[], Awaitable[Mapping[str, object]]]
MessagesPageFetch = Callable[[str | None], Awaitable[Mapping[str, object]]]
MessageMetadataFetch = Callable[[str], Awaitable[Mapping[str, object]]]
HistoryFetch = Callable[[str, str | None], Awaitable[Mapping[str, object]]]


@dataclass(frozen=True)
class GmailBootstrapEvidence:
    next_history_id: str = field(repr=False)
    changed_records: tuple[ChangeRef, ...] = field(repr=False)
    page_count: int
    message_count: int
    history_record_count: int
    digest: str
    initial_baseline: bool = True
    exhausted: bool = True
    checkpoint_persisted: bool = False
    complete_mailbox_history: bool = False
    cross_channel_mapping_verified: bool = False
    reply_absence_proven: bool = False
    outbound_action_approved: bool = False


async def collect_gmail_bootstrap(
    *,
    fetch_profile: ProfileFetch,
    fetch_messages_page: MessagesPageFetch,
    fetch_message_metadata: MessageMetadataFetch,
    fetch_history_page: HistoryFetch,
) -> GmailBootstrapEvidence:
    """Complete a full metadata snapshot then reconcile from its starting cursor.

    The initiating Gmail getProfile historyId is obtained BEFORE listing any
    message. Each messages.list reference is verified with messages.get
    (format=metadata, fields=id,threadId,historyId) so no body is retrieved.
    Then the entire users.history.list page walk is consumed from that SAME
    initiating cursor, including concurrent additions and deletions.

    An invalid/expired anchor, a changed employee authorization, an ambiguous
    page, truncated listing or an oversized mailbox is a failed bootstrap.
    No checkpoint writer is called by this proof collector.
    """
    if not all(callable(f) for f in (
        fetch_profile, fetch_messages_page,
        fetch_message_metadata, fetch_history_page,
    )):
        raise ReplayUnavailable("Authenticated Gmail bootstrap readers unavailable")
    try:
        profile = await fetch_profile()
    except Exception:
        raise ReplayUnavailable("Gmail bootstrap profile unavailable") from None
    if not isinstance(profile, Mapping):
        raise ReplayUnavailable("Gmail bootstrap profile malformed")
    anchor = _history_id(profile.get("historyId"))

    cursor: str | None = None
    used_cursors: set[str] = set()
    seen_ids: set[str] = set()
    history_snapshot: list[object] = []
    snapshot: list[ChangeRef] = []
    page_count = 0
    for index in range(MAX_SNAPSHOT_PAGES):
        if cursor is not None:
            if cursor in used_cursors:
                raise ReplayUnavailable("Gmail bootstrap page cursor repeated")
            used_cursors.add(cursor)
        try:
            page = await fetch_messages_page(cursor)
        except Exception:
            raise ReplayUnavailable("Gmail bootstrap list unavailable") from None
        if not isinstance(page, Mapping):
            raise ReplayUnavailable("Gmail bootstrap page malformed")
        rows = page.get("messages")
        if rows is None:
            if (page.get("resultSizeEstimate") == 0
                    and page.get("nextPageToken") is None):
                rows = []
            else:
                raise ReplayUnavailable("Gmail bootstrap list missing message refs")
        if not isinstance(rows, list):
            raise ReplayUnavailable("Gmail bootstrap message refs invalid")
        if len(rows) > 100 or len(rows) + len(seen_ids) > MAX_SNAPSHOT_MESSAGES:
            raise ReplayUnavailable("Gmail bootstrap exceeds bounded mailbox window")
        serialized: list[object] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReplayUnavailable("Gmail bootstrap message ref malformed")
            identifier = _text(row.get("id"), "Gmail message ID", limit=256)
            thread = row.get("threadId")
            if thread is not None:
                thread = _text(thread, "Gmail thread ID", limit=256)
            if identifier in seen_ids:
                raise ReplayUnavailable("Gmail bootstrap message ID repeated")
            seen_ids.add(identifier)
            try:
                message = await fetch_message_metadata(identifier)
            except Exception:
                raise ReplayUnavailable("Gmail bootstrap metadata retrieval incomplete") from None
            if (not isinstance(message, Mapping)
                    or message.get("id") != identifier):
                raise ReplayUnavailable("Gmail bootstrap source message mismatch")
            real_thread = message.get("threadId")
            if real_thread is not None:
                real_thread = _text(real_thread, "Gmail metadata thread", limit=256)
            if thread is not None and real_thread != thread:
                raise ReplayUnavailable("Gmail bootstrap thread identity changed")
            _history_id(message.get("historyId"))
            snapshot.append(ChangeRef(identifier, "added", real_thread))
            serialized.append([identifier, real_thread])
        next_cursor = page.get("nextPageToken")
        if next_cursor is not None:
            next_cursor = _text(next_cursor, "Gmail bootstrap pageToken")
            if (next_cursor == cursor or next_cursor in used_cursors or not rows):
                raise ReplayUnavailable("Gmail bootstrap listing stalled")
        history_snapshot.append([cursor, next_cursor, serialized])
        page_count = index + 1
        if next_cursor is None:
            break
        cursor = next_cursor
    else:
        raise ReplayUnavailable("Gmail bootstrap exceeds page bound")

    # The fetched baseline cannot stand on its own: concurrent changes may
    # have occurred at any point during the listing and metadata reads.
    # The Gmail history reader is independently bounded and chronological.
    replay = await replay_gmail_history(
        start_history_id=anchor, fetch_page=fetch_history_page,
    )
    combined = tuple(snapshot) + replay.changed_records
    if len(combined) > MAX_INITIAL_EVENTS:
        raise ReplayUnavailable("Gmail bootstrap plus changes exceed event bound")
    material = json.dumps(
        ["gmail-bootstrap-v1", anchor, history_snapshot, replay.digest,
         replay.next_history_id],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return GmailBootstrapEvidence(
        next_history_id=replay.next_history_id,
        changed_records=combined,
        page_count=page_count + replay.page_count,
        message_count=len(snapshot),
        history_record_count=replay.record_count,
        digest=hashlib.sha256(material).hexdigest(),
    )
