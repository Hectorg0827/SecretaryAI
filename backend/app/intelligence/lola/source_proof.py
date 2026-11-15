"""Read-only Gmail / Microsoft Graph page-walk and change-checkpoint verification.

A fully paginated inbox listing alone cannot prove an uninterrupted history of
mailbox changes. Only a separately persisted and validated gap-free change
replay can establish a bounded source watermark. This module does not read
tokens, make network calls, change the DB or authorize any follow-up or send.
Its inputs must come from authenticated server-side adapters, NOT an HTTP
client, LLM response or a forged browser payload.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence
from urllib.parse import urlsplit

MAX_PAGES = 100
MAX_MESSAGES = 500
MAX_SYNC_LAG = timedelta(minutes=10)
MAX_WINDOW = timedelta(days=60)


class SourceProofUnavailable(ValueError):
    """The provider evidence is missing or internally inconsistent."""


@dataclass(frozen=True)
class EmailMessageRef:
    source_id: str
    thread_id: str | None


@dataclass(frozen=True)
class EmailPage:
    provider: str
    requested_cursor: str | None
    next_cursor: str | None
    message_refs: tuple[EmailMessageRef, ...]
    authenticated_provider_request: bool
    mailbox_identity_verified: bool


@dataclass(frozen=True)
class DurableCheckpoint:
    company_id: str
    employee_id: str
    provider: str
    provider_identity_ref: str
    mapping_revision: str
    source_revision: str
    window_start: datetime
    checked_through: datetime
    replayed_through: datetime
    persisted_at: datetime
    page_walk_digest: str
    replay_gap_free: bool
    replay_checkpoint_persisted: bool
    source_authorization_current: bool


@dataclass(frozen=True)
class EmailSourceProof:
    provider: str
    provider_identity_ref: str
    mapping_revision: str
    source_revision: str
    page_count: int
    message_count: int
    coverage_window_start: str
    checked_through: str
    source_digest: str
    page_walk_exhausted: bool
    change_replay_persisted: bool
    provider_message_body_selected: bool = False
    message_contact_verified: bool = False
    cross_channel_case_verified: bool = False
    follow_up_authorized: bool = False
    external_writes_executed: bool = False


def _required(value: object, label: str, limit: int = 2048) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(ch) < 32 for ch in value)):
        raise SourceProofUnavailable(f"Invalid {label}")
    return value


def _maybe_cursor(value: object, label: str) -> str | None:
    return None if value is None else _required(value, label)


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SourceProofUnavailable(f"Invalid {label}")
    return value.astimezone(timezone.utc)


def _refs(records: object, provider: str) -> tuple[EmailMessageRef, ...]:
    if not isinstance(records, list) or len(records) > MAX_MESSAGES:
        raise SourceProofUnavailable("Message metadata page malformed")
    refs = []
    for item in records:
        if not isinstance(item, Mapping):
            raise SourceProofUnavailable("Message metadata malformed")
        identity = _required(item.get("id"), "provider message id")
        thread = item.get("threadId") if provider == "gmail" else item.get("conversationId")
        if thread is not None:
            thread = _required(thread, "conversation id")
        if provider == "outlook" and "@removed" in item:
            raise SourceProofUnavailable("Delta deletion is not a message listing")
        refs.append(EmailMessageRef(identity, thread))
    return tuple(refs)


def parse_gmail_page(
    response: Mapping[str, object],
    *,
    requested_cursor: str | None,
    provider_authenticated: bool,
    mailbox_identity_verified: bool,
) -> EmailPage:
    """Read Gmail messages.list metadata; estimates are never exact coverage."""
    if not isinstance(response, Mapping):
        raise SourceProofUnavailable("Gmail page malformed")
    messages = response.get("messages")
    if messages is None:
        if response.get("resultSizeEstimate") != 0 or response.get("nextPageToken") is not None:
            raise SourceProofUnavailable("Ambiguous missing Gmail messages")
        messages = []
    token = _maybe_cursor(response.get("nextPageToken"), "Gmail next page cursor")
    refs = _refs(messages, "gmail")
    if token is not None and not refs:
        raise SourceProofUnavailable("Gmail page made no pagination progress")
    return EmailPage(
        provider="gmail",
        requested_cursor=_maybe_cursor(requested_cursor, "Gmail requested cursor"),
        next_cursor=token,
        message_refs=refs,
        authenticated_provider_request=provider_authenticated is True,
        mailbox_identity_verified=mailbox_identity_verified is True,
    )


def _graph_url(url: object) -> str:
    raw = _required(url, "Microsoft Graph continuation")
    try:
        value = urlsplit(raw)
    except ValueError:
        raise SourceProofUnavailable("Graph continuation URL malformed") from None
    if (value.scheme != "https"
            or value.netloc != "graph.microsoft.com"
            or value.path != "/v1.0/me/messages"
            or not value.query or value.fragment):
        raise SourceProofUnavailable("Graph continuation outside allowed mailbox endpoint")
    return raw


def parse_graph_page(
    response: Mapping[str, object],
    *,
    requested_cursor: str | None,
    provider_authenticated: bool,
    mailbox_identity_verified: bool,
) -> EmailPage:
    """Read Graph /me/messages metadata using the provider's opaque nextLink."""
    if not isinstance(response, Mapping) or "@odata.deltaLink" in response:
        raise SourceProofUnavailable("Graph page malformed or not a listing")
    next_value = response.get("@odata.nextLink")
    next_cursor = _graph_url(next_value) if next_value is not None else None
    prev = _graph_url(requested_cursor) if requested_cursor is not None else None
    refs = _refs(response.get("value"), "outlook")
    if next_cursor is not None and not refs:
        raise SourceProofUnavailable("Graph continuation did not advance")
    return EmailPage(
        provider="outlook",
        requested_cursor=prev,
        next_cursor=next_cursor,
        message_refs=refs,
        authenticated_provider_request=provider_authenticated is True,
        mailbox_identity_verified=mailbox_identity_verified is True,
    )


def walk_digest(pages: Sequence[EmailPage], provider: str) -> str:
    """Hash exact pagination/cursor metadata without exposing raw source IDs."""
    if provider not in ("gmail", "outlook") or not 1 <= len(pages) <= MAX_PAGES:
        raise SourceProofUnavailable("Invalid provider page walk")
    expected = None
    seen_cursors: set[str] = set()
    seen_messages: set[str] = set()
    serial: list[dict] = []
    count = 0
    for index, page in enumerate(pages):
        if (not isinstance(page, EmailPage)
                or page.provider != provider
                or page.authenticated_provider_request is not True
                or page.mailbox_identity_verified is not True
                or page.requested_cursor != expected
                or not isinstance(page.message_refs, tuple)):
            raise SourceProofUnavailable("Provider page scope or ordering invalid")
        if expected is not None:
            if expected in seen_cursors:
                raise SourceProofUnavailable("Provider pagination cursor repeated")
            seen_cursors.add(expected)
        count += len(page.message_refs)
        if count > MAX_MESSAGES:
            raise SourceProofUnavailable("Provider page walk exceeds record limit")
        for msg in page.message_refs:
            if not isinstance(msg, EmailMessageRef):
                raise SourceProofUnavailable("Provider message reference invalid")
            key = _required(msg.source_id, "message ID")
            if key in seen_messages:
                raise SourceProofUnavailable("Provider duplicated message across pages")
            seen_messages.add(key)
            if msg.thread_id is not None:
                _required(msg.thread_id, "thread ID")
        serial.append({
            "requested": page.requested_cursor,
            "next": page.next_cursor,
            "messages": [[x.source_id, x.thread_id] for x in page.message_refs],
        })
        expected = page.next_cursor
        if expected is None and index != len(pages) - 1:
            raise SourceProofUnavailable("Provider pages continue after exhausted cursor")
        if expected is not None and expected in seen_cursors:
            raise SourceProofUnavailable("Provider pagination loop")
    if expected is not None:
        raise SourceProofUnavailable("Provider pages not exhausted")
    return hashlib.sha256(json.dumps(
        {"provider": provider, "pages": serial},
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def verify_email_source(
    *,
    company_id: str,
    employee_id: str,
    provider: str,
    provider_identity_ref: str,
    mapping_revision: str,
    pages: Sequence[EmailPage],
    checkpoint: DurableCheckpoint,
    as_of: datetime,
) -> EmailSourceProof:
    """Validate page exhaustion AND a separately persisted gap-free change replay.

    The checkpoint must have come from a trusted tenant/employee-scoped
    persistence read with provider-specific change token reconciliation.
    Client-supplied flags are never sufficient.
    """
    for item, label in (
        (company_id, "company"), (employee_id, "employee"),
        (provider_identity_ref, "provider account"),
        (mapping_revision, "mapping revision"),
    ):
        _required(item, label)
    if not isinstance(checkpoint, DurableCheckpoint) or provider not in ("gmail", "outlook"):
        raise SourceProofUnavailable("Provider checkpoint missing")
    now = _utc(as_of, "evaluation time")
    digest = walk_digest(pages, provider)
    for expected, actual in (
        (company_id, checkpoint.company_id),
        (employee_id, checkpoint.employee_id),
        (provider, checkpoint.provider),
        (provider_identity_ref, checkpoint.provider_identity_ref),
        (mapping_revision, checkpoint.mapping_revision),
        (digest, checkpoint.page_walk_digest),
    ):
        if not isinstance(actual, str) or expected != actual:
            raise SourceProofUnavailable("Checkpoint identity or digest mismatch")
    revision = _required(checkpoint.source_revision, "checkpoint revision")
    start = _utc(checkpoint.window_start, "window start")
    end = _utc(checkpoint.checked_through, "verified watermark")
    replay = _utc(checkpoint.replayed_through, "replay watermark")
    persisted = _utc(checkpoint.persisted_at, "persistence timestamp")
    if (start >= end or end > now or now - end > MAX_SYNC_LAG
            or end - start > MAX_WINDOW
            or replay < end or replay > now or persisted < replay or persisted > now):
        raise SourceProofUnavailable("Provider checkpoint window stale or inconsistent")
    if (checkpoint.replay_gap_free is not True
            or checkpoint.replay_checkpoint_persisted is not True
            or checkpoint.source_authorization_current is not True):
        raise SourceProofUnavailable("Source replay is incomplete, unpersisted or unauthorized")
    return EmailSourceProof(
        provider=provider, provider_identity_ref=provider_identity_ref,
        mapping_revision=mapping_revision, source_revision=revision,
        page_count=len(pages),
        message_count=sum(len(x.message_refs) for x in pages),
        coverage_window_start=start.isoformat(),
        checked_through=end.isoformat(), source_digest=digest,
        page_walk_exhausted=True, change_replay_persisted=True,
    )
