"""Authenticated, read-only Gmail history and Graph mail-folder delta replay.

Provider API page fetchers are injected trusted backend transports. This module
does NOT request OAuth credentials, persist any cursor, authorize follow-ups,
or create a network connection. In particular, collecting an exhausted change
round is NOT an atomic durable checkpoint, and neither a bootstrap snapshot
nor a completed delta round proves the absence of customer replies.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping
from urllib.parse import quote, urlsplit

PAGE_LIMIT = 100
CHANGE_LIMIT = 500
CURSOR_LIMIT = 4096


class ReplayUnavailable(ValueError):
    """Provider response, authorization or cursor evidence cannot be trusted."""


class GmailHistoryExpired(ReplayUnavailable):
    """Trusted transport mapped Gmail history.list HTTP 404 to full resync."""


@dataclass(frozen=True)
class ChangeRef:
    source_id: str = field(repr=False)
    operation: str
    # Gmail thread / Graph conversation IDs are not proof of contact linkage.
    thread_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class GmailReplay:
    # Persist this ONLY in the same transaction as all change records.
    next_history_id: str = field(repr=False)
    changed_records: tuple[ChangeRef, ...] = field(repr=False)
    page_count: int
    record_count: int
    digest: str
    exhausted: bool = True
    checkpoint_persisted: bool = False
    cross_channel_mapping_verified: bool = False
    reply_absence_proven: bool = False


@dataclass(frozen=True)
class GraphReplay:
    # Opaque deltaLink must be encrypted at rest by a privileged store.
    next_delta_url: str = field(repr=False)
    changed_records: tuple[ChangeRef, ...] = field(repr=False)
    folder_id: str = field(repr=False)
    page_count: int
    record_count: int
    digest: str
    initial_baseline: bool
    exhausted: bool = True
    checkpoint_persisted: bool = False
    cross_channel_mapping_verified: bool = False
    reply_absence_proven: bool = False


GmailFetch = Callable[[str, str | None], Awaitable[Mapping[str, object]]]
GraphFetch = Callable[[str], Awaitable[Mapping[str, object]]]


def _text(value: object, label: str, *, limit: int = CURSOR_LIMIT) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > limit
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ReplayUnavailable("Invalid provider " + label)
    return value


def _history_id(value: object) -> str:
    token = _text(value, "history cursor", limit=40)
    if not token.isascii() or not token.isdecimal() or int(token) <= 0:
        raise ReplayUnavailable("Invalid Gmail history cursor")
    return token


def _hash(provider: str, origin: str, states: list[object], end: str) -> str:
    data = json.dumps(
        [provider, origin, states, end],
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


async def replay_gmail_history(
    *,
    start_history_id: str,
    fetch_page: GmailFetch,
) -> GmailReplay:
    """Exhaust Gmail history.list from a backend-stored startHistoryId.

    Gmail IDs increase but are intentionally NOT contiguous. Never infer a
    gap from non-adjacent numeric IDs. The trusted fetcher must authenticate
    the actual employee's verified mailbox for EVERY request, set
    startHistoryId to the SAME starting cursor on each page, and reject
    non-2xx results; map only a known Gmail 404 to GmailHistoryExpired.
    """
    start = _history_id(start_history_id)
    if not callable(fetch_page):
        raise ReplayUnavailable("Gmail authorized fetcher unavailable")
    page_token = None
    requested: set[str] = set()
    seen_records: set[str] = set()
    changes: list[ChangeRef] = []
    page_metadata: list[object] = []
    last_watermark = int(start)
    last_record_history_id = int(start)
    last_response_watermark = start

    for page_number in range(1, PAGE_LIMIT + 1):
        if page_token is not None:
            if page_token in requested:
                raise ReplayUnavailable("Gmail pagination loop")
            requested.add(page_token)
        try:
            response = await fetch_page(start, page_token)
        except GmailHistoryExpired:
            raise
        except Exception:
            raise ReplayUnavailable("Gmail authorized history read unavailable") from None
        if not isinstance(response, Mapping):
            raise ReplayUnavailable("Gmail history response malformed")
        watermark = _history_id(response.get("historyId"))
        if int(watermark) < last_watermark:
            raise ReplayUnavailable("Gmail history watermark regressed")
        last_watermark = int(watermark)
        last_response_watermark = watermark
        rows = response.get("history", [])
        if not isinstance(rows, list) or len(rows) + len(seen_records) > CHANGE_LIMIT:
            raise ReplayUnavailable("Gmail history exceeds bounded record count")
        serial: list[object] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReplayUnavailable("Malformed Gmail change record")
            history_id = _history_id(row.get("id"))
            if (int(history_id) <= last_record_history_id
                    or int(history_id) > int(watermark)
                    or history_id in seen_records):
                raise ReplayUnavailable("Gmail history records out of order or out of scope")
            last_record_history_id = int(history_id)
            seen_records.add(history_id)
            operations: list[object] = []
            # Use the documented change collections rather than the summary
            # 'messages' array, which can duplicate child operation entries.
            for key, kind in (
                ("messagesAdded", "added"), ("messagesDeleted", "deleted"),
                ("labelsAdded", "label_changed"), ("labelsRemoved", "label_changed"),
            ):
                group = row.get(key, [])
                if not isinstance(group, list):
                    raise ReplayUnavailable("Gmail change collection malformed")
                for change in group:
                    if not isinstance(change, Mapping):
                        raise ReplayUnavailable("Gmail message change malformed")
                    metadata = change.get("message")
                    if not isinstance(metadata, Mapping):
                        raise ReplayUnavailable("Gmail change message identity unavailable")
                    message_id = _text(metadata.get("id"), "Gmail message ID", limit=256)
                    thread = metadata.get("threadId")
                    if thread is not None:
                        thread = _text(thread, "Gmail thread ID", limit=256)
                    changes.append(ChangeRef(message_id, kind, thread))
                    operations.append([kind, message_id, thread])
                    if len(changes) > CHANGE_LIMIT:
                        raise ReplayUnavailable("Gmail changes exceed processing bound")
            serial.append([history_id, operations])
        token = response.get("nextPageToken")
        if token is not None:
            token = _text(token, "Gmail nextPageToken")
            if token == page_token or token in requested or not rows:
                raise ReplayUnavailable("Gmail history pagination stalled")
        page_metadata.append([page_token, token, watermark, serial])
        if token is None:
            return GmailReplay(
                next_history_id=last_response_watermark,
                changed_records=tuple(changes),
                page_count=page_number,
                record_count=len(seen_records),
                digest=_hash("gmail-history", start, page_metadata, last_response_watermark),
            )
        page_token = token
    raise ReplayUnavailable("Gmail history exceeds page bound")


def _graph_url(url: object, folder_id: str) -> str:
    raw = _text(url, "Graph folder delta URL")
    if chr(92) in raw or " " in raw:
        raise ReplayUnavailable("Unsafe Graph delta URL")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ReplayUnavailable("Invalid Graph delta URL") from None
    if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
            or parsed.fragment or not parsed.query):
        raise ReplayUnavailable("Untrusted Graph delta origin or cursor")
    safe_id = quote(folder_id, safe="")
    valid_paths = {
        "/v1.0/me/mailFolders/" + folder_id + "/messages/delta",
        "/v1.0/me/mailFolders/" + safe_id + "/messages/delta",
        "/v1.0/me/mailfolders/" + folder_id + "/messages/delta",
        "/v1.0/me/mailfolders/" + safe_id + "/messages/delta",
        "/v1.0/me/mailfolders('" + folder_id + "')/messages/delta",
        "/v1.0/me/mailfolders('" + safe_id + "')/messages/delta",
        "/v1.0/me/mailFolders('" + folder_id + "')/messages/delta",
        "/v1.0/me/mailFolders('" + safe_id + "')/messages/delta",
    }
    if parsed.path not in valid_paths:
        raise ReplayUnavailable("Graph delta cursor belongs to another folder")
    return raw


async def replay_graph_folder_delta(
    *,
    verified_folder_id: str,
    fetch_url: GraphFetch,
    previous_delta_url: str | None = None,
) -> GraphReplay:
    """Complete a Microsoft Graph per-folder message delta round.

    Every continuation comes from the server and is pinned to this mailbox
    folder. A terminal deltaLink is mandatory. A missing previous token is a
    bootstrap baseline, NOT an incremental completeness guarantee.
    """
    folder = _text(verified_folder_id, "folder identity", limit=256)
    if "/" in folder or chr(92) in folder or "'" in folder:
        raise ReplayUnavailable("Untrusted Graph folder identity")
    if not callable(fetch_url):
        raise ReplayUnavailable("Graph authorized fetcher unavailable")
    first = previous_delta_url is None
    cursor = (_graph_url(previous_delta_url, folder) if previous_delta_url
              else "https://graph.microsoft.com/v1.0/me/mailFolders/"
              + quote(folder, safe="") + "/messages/delta?$select=id,conversationId")
    starting_cursor = cursor
    observed_urls: set[str] = set()
    serial: list[object] = []
    changes: list[ChangeRef] = []
    seen_message_states: set[tuple[str, str]] = set()
    for page_number in range(1, PAGE_LIMIT + 1):
        if cursor in observed_urls:
            raise ReplayUnavailable("Graph delta continuation repeated")
        observed_urls.add(cursor)
        try:
            response = await fetch_url(cursor)
        except Exception:
            raise ReplayUnavailable("Graph authorized delta request unavailable") from None
        if not isinstance(response, Mapping):
            raise ReplayUnavailable("Graph delta response malformed")
        rows = response.get("value")
        if not isinstance(rows, list) or len(rows) + len(changes) > CHANGE_LIMIT:
            raise ReplayUnavailable("Graph delta messages exceed processing bound")
        next_link = response.get("@odata.nextLink")
        final_link = response.get("@odata.deltaLink")
        if (next_link is None) == (final_link is None):
            raise ReplayUnavailable("Graph delta must return one continuation or terminal checkpoint")
        if next_link is not None:
            next_link = _graph_url(next_link, folder)
        if final_link is not None:
            final_link = _graph_url(final_link, folder)
        events = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReplayUnavailable("Graph delta message malformed")
            message_id = _text(row.get("id"), "Graph message ID", limit=256)
            removed = row.get("@removed")
            if removed is not None and not isinstance(removed, Mapping):
                raise ReplayUnavailable("Invalid Graph removal metadata")
            operation = "deleted" if removed is not None else "changed"
            if (message_id, operation) in seen_message_states:
                raise ReplayUnavailable("Graph repeated same event state")
            seen_message_states.add((message_id, operation))
            thread = row.get("conversationId")
            if thread is not None:
                thread = _text(thread, "Graph conversation ID", limit=256)
            changes.append(ChangeRef(message_id, operation, thread))
            events.append([message_id, operation, thread])
        serial.append([cursor, next_link, final_link, events])
        if final_link is not None:
            return GraphReplay(
                next_delta_url=final_link, changed_records=tuple(changes),
                folder_id=folder, page_count=page_number,
                record_count=len(changes), initial_baseline=first,
                digest=_hash("graph-folder-delta", starting_cursor, serial, final_link),
            )
        if next_link == cursor or next_link in observed_urls:
            raise ReplayUnavailable("Graph delta pagination stalled")
        cursor = next_link
    raise ReplayUnavailable("Graph delta exceeds page bound")
