"""Staging-only, read-only provider change-replay rounds for Lola.

Google Gmail: history.list with an existing historyId, all nextPageToken pages,
and final historyId. A 404 means the history cursor has expired and full
synchronization is required. Gmail history IDs are increasing, not contiguous.

Microsoft Graph: messages/delta is scoped to ONE folder. Every nextLink and
terminal deltaLink must stay on that exact authorized folder, be followed
verbatim, and pass the same mailbox authorization as the opening request.

These collectors never load credentials, select email content, modify provider
data, or advance a database cursor. Their output is an UNCOMMITTED candidate
for a separate atomic checkpoint repository; even complete provider rounds
are never proof of contact identity, WhatsApp coverage, or an unanswered reply.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio
from typing import Awaitable, Callable, Mapping
from urllib.parse import quote, urlsplit

MAX_PAGES = 100
MAX_CHANGES = 5000
MAX_TOKEN_LENGTH = 8192
_GMAIL_CHANGE_FIELDS = {
    "messagesAdded": "added",
    "messagesDeleted": "deleted",
    "labelsAdded": "label_added",
    "labelsRemoved": "label_removed",
}


class DeltaUnavailable(RuntimeError):
    """Provider round incomplete or not verifiable; do not advance checkpoint."""


class DeltaCursorExpired(DeltaUnavailable):
    """The provider cursor expired: isolate this source and require full resync."""


@dataclass(frozen=True)
class Scope:
    company_id: str
    employee_id: str
    provider_connection_ref: str
    provider_identity_ref: str
    mailbox_ref: str
    mapping_revision: str
    provider: str
    folder_id: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.company_id, self.employee_id, self.provider_connection_ref,
            self.provider_identity_ref, self.mailbox_ref, self.mapping_revision,
        ):
            _required(value, "source identity", 256)
        if self.provider not in ("gmail", "outlook"):
            raise DeltaUnavailable("Unsupported employee mailbox provider")
        if self.provider == "outlook":
            _required(self.folder_id, "Graph folder ID", 256)
        elif self.folder_id is not None:
            raise DeltaUnavailable("Gmail change cursor cannot contain a Graph folder")


@dataclass(frozen=True)
class SourceChange:
    kind: str
    message_id: str = field(repr=False)
    thread_id: str | None = field(default=None, repr=False)
    history_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DeltaCandidate:
    scope: Scope = field(repr=False)
    previous_cursor: str = field(repr=False)
    terminal_cursor: str = field(repr=False)
    changes: tuple[SourceChange, ...] = field(repr=False)
    source_digest: str
    pages_processed: int
    changes_processed: int
    observed_at: datetime
    round_complete: bool = True
    gap_free_from_prior_cursor: bool = True
    checkpoint_persisted: bool = False
    mailbox_history_certified_for_followup: bool = False
    external_writes_executed: bool = False


GmailReadPage = Callable[[str, str | None], Awaitable[Mapping[str, object]]]
GraphReadPage = Callable[[str], Awaitable[Mapping[str, object]]]


def _required(value: object, label: str, maximum: int = MAX_TOKEN_LENGTH) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(c) < 32 for c in value)):
        raise DeltaUnavailable(f"Provider {label} invalid")
    return value


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DeltaUnavailable("Provider read requires an aware evaluation clock")
    instant = value.astimezone(timezone.utc)
    return instant


def _history(value: object, label: str) -> str:
    raw = _required(value, label, 25)
    if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
        raise DeltaUnavailable("Gmail history ID invalid")
    return raw


def _graph_url(value: object, folder_id: str) -> str:
    raw = _required(value, "Graph delta URL")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise DeltaUnavailable("Graph delta URL malformed") from None
    expected = "/v1.0/me/mailFolders/" + quote(folder_id, safe="") + "/messages/delta"
    if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
            or parsed.path != expected or parsed.fragment):
        raise DeltaUnavailable("Graph delta URL belongs to another mailbox or folder")
    # Exact query tokens must be preserved rather than parsed or reconstructed.
    if not parsed.query:
        raise DeltaUnavailable("Graph delta state token missing")
    return raw


def _initial_graph_url(folder_id: str) -> str:
    return (
        "https://graph.microsoft.com/v1.0/me/mailFolders/"
        + quote(folder_id, safe="")
        + "/messages/delta?$select=id,conversationId"
    )


def _digest(scope: Scope, previous: str, terminal: str, trace: list[object]) -> str:
    material = json.dumps(
        {
            "scope": [
                scope.company_id, scope.employee_id, scope.provider,
                scope.provider_connection_ref, scope.provider_identity_ref,
                scope.mailbox_ref, scope.folder_id, scope.mapping_revision,
            ],
            "previous": previous, "terminal": terminal, "pages": trace,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def collect_gmail_history(
    *,
    scope: Scope,
    start_history_id: str,
    read_page: GmailReadPage,
    as_of: datetime,
) -> DeltaCandidate:
    """Replay Gmail history pages. The authenticated reader is injected.

    A Gmail HTTP 404 must be translated by that reader into
    DeltaCursorExpired; this collector will not silently rebuild a baseline.
    """
    if scope.provider != "gmail":
        raise DeltaUnavailable("Gmail reader requires a Gmail connection")
    initial = _history(start_history_id, "starting history ID")
    current = int(initial)
    now = _utc(as_of)
    token = None
    cursor_seen: set[str] = set()
    history_seen: set[int] = set()
    trace: list[object] = []
    changes: list[SourceChange] = []
    last_terminal = initial
    last_watermark = int(initial)
    for page_num in range(1, MAX_PAGES + 1):
        try:
            body = await asyncio.wait_for(read_page(initial, token), timeout=15)
        except DeltaCursorExpired:
            raise
        except Exception:
            raise DeltaUnavailable("Gmail history API unavailable") from None
        if not isinstance(body, Mapping):
            raise DeltaUnavailable("Gmail history page malformed")
        response_history = _history(body.get("historyId"), "response history ID")
        response_end = int(response_history)
        if response_end < current or response_end < last_watermark:
            raise DeltaUnavailable("Gmail history watermark regressed")
        last_watermark = response_end
        records = body.get("history", [])
        if not isinstance(records, list):
            raise DeltaUnavailable("Gmail history entries malformed")
        if len(records) + len(changes) > MAX_CHANGES:
            raise DeltaUnavailable("Gmail history replay exceeds bounded scope")
        serialized: list[object] = []
        for entry in records:
            if not isinstance(entry, Mapping):
                raise DeltaUnavailable("Gmail history entry malformed")
            record_history = _history(entry.get("id"), "record history ID")
            index = int(record_history)
            if index <= int(initial) or index in history_seen or index < current:
                raise DeltaUnavailable("Gmail history records out of order or duplicated")
            if index > response_end:
                raise DeltaUnavailable("Gmail history record exceeds response watermark")
            history_seen.add(index)
            current = index
            all_changes: list[list[str | None]] = []
            for field_name, kind in _GMAIL_CHANGE_FIELDS.items():
                events = entry.get(field_name, [])
                if not isinstance(events, list):
                    raise DeltaUnavailable("Gmail change list malformed")
                for event in events:
                    msg = event.get("message") if isinstance(event, Mapping) else None
                    if not isinstance(msg, Mapping):
                        raise DeltaUnavailable("Gmail history lacks a message reference")
                    mid = _required(msg.get("id"), "message ID", 1024)
                    tid = msg.get("threadId")
                    if tid is not None:
                        _required(tid, "thread ID", 1024)
                    changes.append(SourceChange(kind, mid, tid, record_history))
                    all_changes.append([kind, mid, tid])
                    if len(changes) > MAX_CHANGES:
                        raise DeltaUnavailable("Gmail history change count exceeded")
            serialized.append([record_history, all_changes])
        last_terminal = response_history
        next_token = body.get("nextPageToken")
        if next_token is None:
            trace.append([token, None, response_history, serialized])
            return DeltaCandidate(
                scope=scope, previous_cursor=initial,
                terminal_cursor=last_terminal, changes=tuple(changes),
                source_digest=_digest(scope, initial, last_terminal, trace),
                pages_processed=page_num, changes_processed=len(changes),
                observed_at=now,
            )
        next_token = _required(next_token, "Gmail page token")
        if next_token == token or next_token in cursor_seen:
            raise DeltaUnavailable("Gmail history pagination stalled or repeated a cursor")
        cursor_seen.add(next_token)
        trace.append([token, next_token, response_history, serialized])
        token = next_token
    raise DeltaUnavailable("Gmail history exceeded page limit")


async def collect_graph_delta(
    *,
    scope: Scope,
    previous_delta_link: str | None,
    read_page: GraphReadPage,
    as_of: datetime,
) -> DeltaCandidate:
    """Collect one per-folder Microsoft Graph message delta round.

    The result may contain new/deleted/updated messages but does NOT
    independently establish inbox-wide coverage, contact linkage or replies.
    """
    if scope.provider != "outlook" or not isinstance(scope.folder_id, str):
        raise DeltaUnavailable("Graph delta requires a verified Outlook folder")
    now = _utc(as_of)
    initial = (_graph_url(previous_delta_link, scope.folder_id)
               if previous_delta_link is not None
               else _initial_graph_url(scope.folder_id))
    current_url = initial
    visited: set[str] = set()
    trace: list[object] = []
    changes: list[SourceChange] = []
    for page_num in range(1, MAX_PAGES + 1):
        if current_url in visited:
            raise DeltaUnavailable("Graph delta cursor repeated")
        visited.add(current_url)
        try:
            body = await asyncio.wait_for(read_page(current_url), timeout=15)
        except Exception:
            raise DeltaUnavailable("Graph delta provider unavailable") from None
        if not isinstance(body, Mapping):
            raise DeltaUnavailable("Graph delta payload malformed")
        refs = body.get("value")
        if not isinstance(refs, list) or len(refs) + len(changes) > MAX_CHANGES:
            raise DeltaUnavailable("Graph delta message batch invalid or over limit")
        serialized = []
        for item in refs:
            if not isinstance(item, Mapping):
                raise DeltaUnavailable("Graph delta message malformed")
            message_id = _required(item.get("id"), "Graph message ID", 1024)
            removed = item.get("@removed")
            if removed is not None and not isinstance(removed, Mapping):
                raise DeltaUnavailable("Graph removal marker malformed")
            kind = "deleted" if removed is not None else "updated_or_added"
            thread = item.get("conversationId")
            if thread is not None:
                _required(thread, "Graph conversation ID", 1024)
            changes.append(SourceChange(kind, message_id, thread))
            serialized.append([kind, message_id, thread])
        next_link = body.get("@odata.nextLink")
        terminal_link = body.get("@odata.deltaLink")
        if (next_link is None) == (terminal_link is None):
            raise DeltaUnavailable("Graph delta must have exactly one continuation or terminal link")
        if terminal_link is not None:
            terminal = _graph_url(terminal_link, scope.folder_id)
            if terminal in visited:
                raise DeltaUnavailable("Graph terminal cursor repeated a request")
            trace.append([current_url, terminal, "delta", serialized])
            return DeltaCandidate(
                scope=scope,
                previous_cursor=previous_delta_link or "",
                terminal_cursor=terminal, changes=tuple(changes),
                source_digest=_digest(scope, previous_delta_link or "", terminal, trace),
                pages_processed=page_num, changes_processed=len(changes),
                observed_at=now,
            )
        next_url = _graph_url(next_link, scope.folder_id)
        if next_url in visited:
            raise DeltaUnavailable("Graph delta pagination stalled")
        trace.append([current_url, next_url, "next", serialized])
        current_url = next_url
    raise DeltaUnavailable("Graph delta exceeded page limit")
