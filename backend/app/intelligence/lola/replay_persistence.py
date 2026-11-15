"""Employee-scoped, encrypted, atomic provider replay checkpoint adapter.

Private backend-only integration; NO API endpoints, background job or automatic
execution. Its trusted caller must resolve the current employee/case first.
No plaintext OAuth token, Gmail cursor, Graph deltaLink, contact or message ID
is sent to Supabase. A successful RPC is confirmed with a scope-matched read.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase
from app.intelligence.lola.incremental_replay import GmailReplay, GraphReplay

_CHECKPOINTS = "lola_provider_replay_checkpoints"
_EVENTS = "lola_provider_replay_events"
_RPC = "lola_commit_provider_replay"
_FIELDS = (
    "company_id,employee_id,provider,provider_identity_sha256,resource_sha256,"
    "mapping_revision,cursor_ciphertext,cursor_sha256,replay_digest,"
    "baseline_only,replayed_through,generation"
)


class CheckpointUnavailable(RuntimeError):
    """A scoped replay checkpoint could not be verified or committed."""


@dataclass(frozen=True)
class MailboxScope:
    """Produced only after backend-authorized current connection verification."""
    company_id: str
    employee_id: str
    provider: str
    provider_identity_ref: str = field(repr=False)
    resource_id: str = field(repr=False)
    mapping_revision: str


@dataclass(frozen=True)
class StoredCheckpoint:
    generation: int
    plaintext_cursor: str = field(repr=False)
    replay_digest: str = field(repr=False)
    baseline_only: bool
    replayed_through: datetime
    # An encrypted durable checkpoint is not full, cross-channel source history.
    complete_mailbox_history: bool = False
    cross_channel_mapping_verified: bool = False
    reply_absence_proven: bool = False


@dataclass(frozen=True)
class CheckpointCommit:
    generation: int
    provider: str
    stored_event_count: int
    replay_digest: str
    replayed_through: str
    # Never infer follow-up authorization from an atomic DB write.
    cursor_encrypted_and_persisted: bool = True
    complete_mailbox_history: bool = False
    cross_channel_mapping_verified: bool = False
    reply_absence_proven: bool = False
    outbound_action_approved: bool = False


def _uuid(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise CheckpointUnavailable("Invalid employee/tenant identity") from None
    if parsed != value:
        raise CheckpointUnavailable("Uncanonical employee/tenant identity")
    return parsed


def _field(value: object, label: str, *, limit: int = 4096) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CheckpointUnavailable("Invalid " + label)
    return value


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CheckpointUnavailable("UTC replay timestamp required")
    return value.astimezone(timezone.utc)


def _key_from_environment() -> bytes:
    """Must be configured in backend secrets only, never in a frontend bundle."""
    raw = os.environ.get("LOLA_PROVIDER_CURSOR_KEY_B64", "")
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise CheckpointUnavailable("Provider cursor encryption key unavailable") from None
    if len(key) != 32:
        raise CheckpointUnavailable("Provider cursor encryption key unavailable")
    return key


class CursorVault:
    """AES-256-GCM sealed envelope with tenant/employee/mailbox-bound AAD."""

    def __init__(self, encryption_key: bytes):
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
            raise CheckpointUnavailable("Invalid cursor vault configuration")
        self._aead = AESGCM(encryption_key)
        # Separate HMAC domain from the encryption domain.
        self._index_key = hmac.digest(encryption_key, b"lola:cursor-index:v1", "sha256")

    @classmethod
    def from_backend_environment(cls) -> "CursorVault":
        return cls(_key_from_environment())

    def digest(self, *parts: str) -> str:
        if not parts:
            raise CheckpointUnavailable("Missing cursor digest scope")
        envelope = json.dumps(parts, separators=(",", ":"), ensure_ascii=True)
        return hmac.new(self._index_key, envelope.encode("utf-8"), hashlib.sha256).hexdigest()

    def _aad(self, scope: MailboxScope) -> bytes:
        _check_scope(scope)
        return json.dumps([
            "lola:provider-cursor:v1",
            scope.company_id, scope.employee_id, scope.provider,
            scope.provider_identity_ref, scope.resource_id, scope.mapping_revision,
        ], separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def encrypt(self, scope: MailboxScope, cursor: str) -> str:
        value = _field(cursor, "provider cursor")
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, value.encode("utf-8"), self._aad(scope))
        return "v1:" + base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, scope: MailboxScope, envelope: str) -> str:
        if not isinstance(envelope, str) or not envelope.startswith("v1:"):
            raise CheckpointUnavailable("Unsupported cursor encryption envelope")
        try:
            payload = base64.b64decode(envelope[3:], validate=True)
            if len(payload) < 29 or len(payload) > 8192:
                raise ValueError("Invalid size")
            value = self._aead.decrypt(payload[:12], payload[12:], self._aad(scope))
            return _field(value.decode("utf-8"), "decrypted provider cursor")
        except (ValueError, InvalidTag, UnicodeError, TypeError):
            raise CheckpointUnavailable("Provider cursor could not be authenticated") from None


def _check_scope(scope: MailboxScope) -> None:
    if not isinstance(scope, MailboxScope) or scope.provider not in ("gmail", "outlook"):
        raise CheckpointUnavailable("Unrecognized source scope")
    _uuid(scope.company_id)
    _uuid(scope.employee_id)
    _field(scope.provider_identity_ref, "verified provider identity", limit=256)
    _field(scope.resource_id, "verified mailbox resource", limit=256)
    _field(scope.mapping_revision, "verified mapping revision", limit=200)


def scope_for_verified_case(
    resolved: ResolvedCrossChannelCase,
    *,
    provider: str,
    resource_id: str,
) -> MailboxScope:
    """The resolved case is a prerequisite, NOT inferred from an email address."""
    if (not isinstance(resolved, ResolvedCrossChannelCase)
            or resolved.source != "backend_verified_registry"
            or resolved.scope.employee_consent_verified is not True
            or resolved.scope.mapping_revision != resolved.source_fingerprint):
        raise CheckpointUnavailable("Backend-verified cross-channel mapping required")
    scope = MailboxScope(
        company_id=resolved.scope.company_id,
        employee_id=resolved.scope.employee_id,
        provider=provider,
        provider_identity_ref=resolved.email_provider_identity_ref,
        resource_id=resource_id,
        mapping_revision=resolved.scope.mapping_revision,
    )
    _check_scope(scope)
    return scope


def _filters(scope: MailboxScope, vault: CursorVault) -> dict[str, str]:
    _check_scope(scope)
    return {
        "company_id": scope.company_id,
        "employee_id": scope.employee_id,
        "provider": scope.provider,
        "provider_identity_sha256": vault.digest(
            scope.company_id, scope.employee_id, scope.provider, scope.provider_identity_ref,
        ),
        "resource_sha256": vault.digest(
            scope.company_id, scope.employee_id, scope.provider,
            scope.provider_identity_ref, scope.resource_id,
        ),
    }


def _read_scoped_row(db: Any, filters: dict[str, str]) -> dict | None:
    """Never read a broad unfiltered table, even using service-role credentials."""
    try:
        query = db.table(_CHECKPOINTS).select(_FIELDS)
        for column, value in filters.items():
            query = query.eq(column, value)
        response = query.limit(2).execute()
        data = response.data
    except Exception:
        raise CheckpointUnavailable("Backend checkpoint source unavailable") from None
    if not isinstance(data, list) or len(data) > 1:
        raise CheckpointUnavailable("Ambiguous provider checkpoint scope")
    if not data:
        return None
    row = data[0]
    if (not isinstance(row, dict)
            or any(str(row.get(key)) != expected for key, expected in filters.items())):
        raise CheckpointUnavailable("Provider checkpoint identity mismatch")
    return row


def read_checkpoint(
    *, db: Any, scope: MailboxScope, vault: CursorVault,
) -> StoredCheckpoint | None:
    filters = _filters(scope, vault)
    row = _read_scoped_row(db, filters)
    if row is None:
        return None
    if row.get("mapping_revision") != scope.mapping_revision:
        raise CheckpointUnavailable("Provider mapping was rotated or revoked")
    generation = row.get("generation")
    if type(generation) is not int or generation < 1:
        raise CheckpointUnavailable("Invalid provider checkpoint generation")
    if type(row.get("baseline_only")) is not bool:
        raise CheckpointUnavailable("Invalid provider baseline state")
    cipher = _field(row.get("cursor_ciphertext"), "cursor ciphertext", limit=8192)
    cursor = vault.decrypt(scope, cipher)
    if row.get("cursor_sha256") != vault.digest("cursor", cursor):
        raise CheckpointUnavailable("Provider cursor integrity mismatch")
    digest = _field(row.get("replay_digest"), "replay digest", limit=64)
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise CheckpointUnavailable("Provider replay digest invalid")
    raw_time = row.get("replayed_through")
    if not isinstance(raw_time, str):
        raise CheckpointUnavailable("Provider checkpoint timestamp unavailable")
    try:
        replayed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        raise CheckpointUnavailable("Provider checkpoint timestamp malformed") from None
    return StoredCheckpoint(
        generation=generation, plaintext_cursor=cursor,
        replay_digest=digest, baseline_only=row["baseline_only"],
        replayed_through=_utc(replayed),
    )


def commit_replay(
    *,
    db: Any, scope: MailboxScope, vault: CursorVault,
    replay: GmailReplay | GraphReplay,
    previously_read: StoredCheckpoint | None,
    observed_at: datetime,
) -> CheckpointCommit:
    """Atomic RPC commit and re-read. No direct writes to the event table.

    An initial Graph delta round may establish ONLY a baseline. Gmail requires
    a pre-existing fully bootstrapped history cursor, created by a separate
    approved full-sync path. Never turn a first Gmail cursor into a fabricated
    complete mailbox snapshot.
    """
    filters = _filters(scope, vault)
    now = _utc(observed_at)
    if (not isinstance(replay, (GmailReplay, GraphReplay))
            or replay.exhausted is not True
            or replay.checkpoint_persisted is not False
            or replay.reply_absence_proven is not False):
        raise CheckpointUnavailable("Invalid or prematurely trusted replay")
    if (scope.provider == "gmail" and not isinstance(replay, GmailReplay)
            or scope.provider == "outlook" and not isinstance(replay, GraphReplay)):
        raise CheckpointUnavailable("Replay provider identity mismatch")
    if replay.page_count < 1 or len(replay.changed_records) > 500:
        raise CheckpointUnavailable("Provider replay record bound violated")
    if (not isinstance(replay.digest, str) or len(replay.digest) != 64
            or any(x not in "0123456789abcdef" for x in replay.digest)):
        raise CheckpointUnavailable("Invalid replay digest")
    if isinstance(replay, GmailReplay):
        if previously_read is None:
            raise CheckpointUnavailable("Gmail requires a verified full-sync baseline")
        cursor = replay.next_history_id
        if not cursor.isdecimal() or int(cursor) <= int(previously_read.plaintext_cursor):
            raise CheckpointUnavailable("Gmail history cursor did not advance")
        baseline = previously_read.baseline_only
    else:
        if replay.folder_id != scope.resource_id:
            raise CheckpointUnavailable("Graph replay folder does not match employee connection")
        if (replay.initial_baseline and previously_read is not None
                or not replay.initial_baseline and previously_read is None):
            raise CheckpointUnavailable("Graph bootstrap/incremental checkpoint mismatch")
        cursor = replay.next_delta_url
        baseline = replay.initial_baseline
    if previously_read is not None and previously_read.replayed_through > now:
        raise CheckpointUnavailable("Provider replay timestamp regressed")
    # Replay events are source metadata only. Key includes replay digest so
    # subsequent updates to a message receive distinct deduplication keys.
    event_rows = []
    keys: set[str] = set()
    for index, event in enumerate(replay.changed_records):
        if event.operation not in ("added", "deleted", "changed", "label_changed"):
            raise CheckpointUnavailable("Unsupported provider change type")
        key = vault.digest(
            "event", scope.company_id, scope.employee_id, scope.provider,
            scope.provider_identity_ref, scope.resource_id,
            replay.digest, str(index), event.source_id, event.operation,
        )
        if key in keys:
            raise CheckpointUnavailable("Duplicate provider event key")
        keys.add(key)
        event_rows.append({"key_sha256": key, "operation": event.operation})
    sealed = vault.encrypt(scope, cursor)
    args = {
        "p_company_id": scope.company_id,
        "p_employee_id": scope.employee_id,
        "p_provider": scope.provider,
        "p_provider_identity_sha256": filters["provider_identity_sha256"],
        "p_resource_sha256": filters["resource_sha256"],
        "p_mapping_revision": scope.mapping_revision,
        "p_expected_generation": previously_read.generation if previously_read else 0,
        "p_cursor_ciphertext": sealed,
        "p_cursor_sha256": vault.digest("cursor", cursor),
        "p_replay_digest": replay.digest,
        "p_baseline_only": baseline,
        "p_replayed_through": now.isoformat(),
        "p_events": event_rows,
    }
    try:
        response = db.rpc(_RPC, args).execute()
        raw_generation = response.data
        # PostgREST installations can represent scalar RPC returns as an
        # integer or a one-item JSON array; reject every other shape.
        generation = (raw_generation[0]
                      if isinstance(raw_generation, list) and len(raw_generation) == 1
                      else raw_generation)
    except Exception:
        raise CheckpointUnavailable("Atomic provider checkpoint commit failed") from None
    if (type(generation) is not int or generation != args["p_expected_generation"] + 1):
        raise CheckpointUnavailable("Checkpoint RPC did not return a valid generation")
    stored = read_checkpoint(db=db, scope=scope, vault=vault)
    if (stored is None or stored.generation != generation
            or stored.plaintext_cursor != cursor
            or stored.replay_digest != replay.digest
            or stored.baseline_only != (
                baseline if previously_read is None
                else (previously_read.baseline_only and baseline)
            )):
        raise CheckpointUnavailable("Committed provider checkpoint did not verify")
    if event_rows:
        try:
            query = db.table(_EVENTS).select("event_key_sha256")
            for key, value in filters.items():
                query = query.eq(key, value)
            data = query.eq("replay_digest", replay.digest).limit(501).execute().data
        except Exception:
            raise CheckpointUnavailable("Committed provider events unavailable") from None
        expected_keys = {item["key_sha256"] for item in event_rows}
        if (not isinstance(data, list)
                or len(data) != len(expected_keys)
                or {x.get("event_key_sha256") for x in data if isinstance(x, dict)}
                != expected_keys):
            raise CheckpointUnavailable("Provider event persistence verification failed")
    return CheckpointCommit(
        generation=generation, provider=scope.provider,
        stored_event_count=len(event_rows), replay_digest=replay.digest,
        replayed_through=stored.replayed_through.isoformat(),
    )
