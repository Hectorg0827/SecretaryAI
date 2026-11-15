"""Fail-closed Supabase RPC implementation of Lola's atomic provider round store.

This is an INTERNAL, unmounted adapter: it does not register a route,
schedule a sync or deploy a database function. In the absence of the reviewed
service-role-only database RPCs, every operation fails closed.

Required RPCs (not yet installed):
  lola_delta_checkpoint_read: tenant/employee/provider/folder-scoped primary
    read returning a single JSON record, or JSON null for absent checkpoint.
  lola_delta_checkpoint_commit: one transaction, current DB employee/consent
    recheck, optimistic expected-version/cursor-DIGEST compare, immutable
    deduped event records, encrypted terminal cursor, revision increment.
    Returns JSON {"committed": boolean}; false means CAS conflict.

The RPC implementation MUST NOT trust client boolean flags. In particular,
the database returns both single-transaction and current-authorization proof
from its OWN transaction, never echoes caller-supplied values. See Stage 9
storage contract docs before applying a migration.

No browser, LLM, provider message bodies, contact names, or OAuth credentials
may be passed into this adapter. Its HMAC refs hide provider message IDs,
while AES-GCM protects opaque Graph delta links/history cursors at rest.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping

from app.intelligence.lola.delta_checkpoint import (
    AtomicRoundStore, CheckpointUnavailable, PersistedRound,
)
from app.intelligence.lola.delta_replay import (
    MAX_CHANGES, DeltaCandidate, Scope, SourceChange,
)
from app.intelligence.lola.identity_registry import _actor
from app.utils.encryption import decrypt, encrypt

CurrentAuthorization = Callable[[Scope], Awaitable[bool]]
RPC_READ = "lola_delta_checkpoint_read"
RPC_COMMIT = "lola_delta_checkpoint_commit"


def _scope_params(scope: Scope) -> dict:
    """Exact scope selectors. These originate in the trusted connector registry."""
    return {
        "p_company_id": scope.company_id,
        "p_employee_id": scope.employee_id,
        "p_provider": scope.provider,
        "p_provider_connection_ref": scope.provider_connection_ref,
        "p_provider_identity_ref": scope.provider_identity_ref,
        "p_mailbox_ref": scope.mailbox_ref,
        "p_folder_id": scope.folder_id,
        "p_mapping_revision": scope.mapping_revision,
    }


def _time(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CheckpointUnavailable(f"Checkpoint {label} malformed") from None
    else:
        raise CheckpointUnavailable(f"Checkpoint {label} missing")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CheckpointUnavailable(f"Checkpoint {label} missing timezone")
    return moment.astimezone(timezone.utc)


def _required(value: object, label: str, limit: int = 8192) -> str:
    if (not isinstance(value, str) or not value or len(value) > limit
            or any(ord(x) < 32 for x in value)):
        raise CheckpointUnavailable(f"Checkpoint {label} invalid")
    return value


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class SupabaseAtomicRoundStore(AtomicRoundStore):
    """RPC-backed, employee-owned delta checkpoint store (NOT enabled by default).

    The Supabase client must be a privileged SERVER-ONLY client. Its only
    callable database operations are the two audited RPCs plus the existing
    active-employee membership query used by _actor. Never use a browser
    publishable key or infer authorizations from stale JWT metadata.

    The connector authorization callback must read CURRENT employee consent,
    account ownership, mapping version and provider scope from trusted
    backend sources on every call; it MUST fail closed when missing.
    """

    def __init__(
        self, *, db, user: dict,
        current_authorization: CurrentAuthorization,
        encryption_key: str, hmac_key: bytes,
    ):
        if (not callable(current_authorization)
                or not isinstance(encryption_key, str)
                or len(encryption_key) < 24
                or not isinstance(hmac_key, bytes) or len(hmac_key) < 32):
            raise ValueError("Durable checkpoint credentials or authorization missing")
        self._db = db
        self._user = user
        self._current_authorization = current_authorization
        self._encryption_key = encryption_key
        self._hmac_key = hmac_key

    def _digest(self, scope: Scope, value: str) -> str:
        material = _json([
            scope.company_id, scope.employee_id, scope.provider,
            scope.provider_connection_ref, scope.provider_identity_ref,
            scope.mailbox_ref, scope.folder_id, scope.mapping_revision,
            value,
        ])
        return hmac.new(self._hmac_key, material.encode(), hashlib.sha256).hexdigest()

    def _event_ref(self, scope: Scope, candidate: DeltaCandidate,
                   index: int, change: SourceChange) -> dict:
        if not isinstance(change, SourceChange):
            raise CheckpointUnavailable("Provider change row malformed")
        if change.kind not in (
            "added", "deleted", "label_added", "label_removed",
            "updated_or_added",
        ):
            raise CheckpointUnavailable("Provider change type unsupported")
        _required(change.message_id, "message ID", 1024)
        if change.thread_id is not None:
            _required(change.thread_id, "thread ID", 1024)
        if change.history_id is not None:
            _required(change.history_id, "history ID", 32)
        material = _json([
            candidate.source_digest, index, change.kind, change.message_id,
            change.thread_id, change.history_id,
        ])
        event_ref = self._digest(scope, material)
        thread_ref = (
            self._digest(scope, "thread:" + change.thread_id)
            if change.thread_id is not None else None
        )
        return {
            "event_ref": event_ref,
            "kind": change.kind,
            "thread_ref": thread_ref,
        }

    async def _verify(self, scope: Scope) -> None:
        if not isinstance(scope, Scope):
            raise CheckpointUnavailable("Checkpoint scope not validated")
        try:
            company_id, employee_id = _actor(self._user, self._db)
        except Exception:
            raise CheckpointUnavailable("Active employee scope unavailable") from None
        if scope.company_id != company_id or scope.employee_id != employee_id:
            raise CheckpointUnavailable("Checkpoint scope not owned by current employee")
        try:
            allowed = await asyncio.wait_for(
                self._current_authorization(scope), timeout=5,
            )
        except Exception:
            raise CheckpointUnavailable("Current provider authorization unavailable") from None
        if allowed is not True:
            raise CheckpointUnavailable("Employee consent, provider or mapping revoked")

    def _rpc(self, name: str, arguments: dict) -> object:
        """Never stringify Supabase errors: they may contain sensitive cursors."""
        try:
            response = self._db.rpc(name, arguments).execute()
            return getattr(response, "data", None)
        except Exception:
            raise CheckpointUnavailable("Atomic checkpoint database RPC unavailable") from None

    async def read_primary(self, scope: Scope) -> PersistedRound | None:
        await self._verify(scope)
        data = self._rpc(RPC_READ, _scope_params(scope))
        if data is None:
            return None
        if isinstance(data, list):
            if len(data) != 1:
                raise CheckpointUnavailable("Checkpoint primary read not unique")
            data = data[0]
        if not isinstance(data, Mapping):
            raise CheckpointUnavailable("Checkpoint primary record malformed")
        for field, expected in (
            ("company_id", scope.company_id),
            ("employee_id", scope.employee_id),
            ("provider", scope.provider),
            ("provider_connection_ref", scope.provider_connection_ref),
            ("provider_identity_ref", scope.provider_identity_ref),
            ("mailbox_ref", scope.mailbox_ref),
            ("folder_id", scope.folder_id),
            ("mapping_revision", scope.mapping_revision),
        ):
            if data.get(field) != expected:
                raise CheckpointUnavailable("Checkpoint primary scope mismatch")
        ciphertext = _required(data.get("cursor_ciphertext"), "encrypted cursor")
        try:
            cursor = decrypt(ciphertext, self._encryption_key)
        except Exception:
            raise CheckpointUnavailable("Encrypted provider cursor cannot be verified") from None
        expected_digest = self._digest(scope, cursor)
        stored_digest = data.get("cursor_digest")
        if (not isinstance(stored_digest, str)
                or not hmac.compare_digest(expected_digest, stored_digest)):
            raise CheckpointUnavailable("Provider cursor digest mismatch")
        version = data.get("version")
        changes = data.get("changes_applied")
        if type(version) is not int or version < 1 or type(changes) is not int or changes < 0:
            raise CheckpointUnavailable("Checkpoint version or change count invalid")
        digest = data.get("last_round_digest")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise CheckpointUnavailable("Checkpoint source digest invalid")
        coverage_start = data.get("source_coverage_start")
        if coverage_start is not None:
            coverage_start = _time(coverage_start, "coverage start")
        return PersistedRound(
            scope=scope, version=version, cursor=cursor,
            last_round_digest=digest, changes_applied=changes,
            committed_at=_time(data.get("committed_at"), "commit timestamp"),
            events_and_cursor_single_transaction=(
                data.get("events_and_cursor_single_transaction") is True
            ),
            current_employee_authorization_checked=(
                data.get("current_employee_authorization_checked") is True
            ),
            source_coverage_start=coverage_start,
        )

    async def commit_atomic(
        self, *, scope: Scope, expected_version: int,
        expected_cursor: str, round_candidate: DeltaCandidate,
    ) -> bool:
        await self._verify(scope)
        if (not isinstance(round_candidate, DeltaCandidate)
                or round_candidate.scope != scope
                or not isinstance(expected_cursor, str)
                or round_candidate.previous_cursor != expected_cursor
                or type(expected_version) is not int or expected_version < 0
                or not isinstance(round_candidate.changes, tuple)
                or len(round_candidate.changes) > MAX_CHANGES):
            raise CheckpointUnavailable("Provider transaction scope invalid")
        # Deterministic keyed event refs permit deduplication without storing
        # Internet Message-ID, a phone number, or raw Graph/Gmail IDs.
        records = [
            self._event_ref(scope, round_candidate, index, change)
            for index, change in enumerate(round_candidate.changes)
        ]
        terminal = _required(round_candidate.terminal_cursor, "new cursor")
        try:
            encrypted_terminal = encrypt(terminal, self._encryption_key)
        except Exception:
            raise CheckpointUnavailable("Could not protect provider delta cursor") from None
        args = {
            **_scope_params(scope),
            "p_expected_version": expected_version,
            "p_expected_cursor_digest": (
                self._digest(scope, expected_cursor) if expected_version else None
            ),
            "p_terminal_cursor_ciphertext": encrypted_terminal,
            "p_terminal_cursor_digest": self._digest(scope, terminal),
            "p_last_round_digest": round_candidate.source_digest,
            "p_changes": records,
            "p_observed_at": round_candidate.observed_at.isoformat(),
        }
        data = self._rpc(RPC_COMMIT, args)
        if not isinstance(data, Mapping) or type(data.get("committed")) is not bool:
            raise CheckpointUnavailable("Ambiguous transaction acknowledgement")
        if set(data) != {"committed"}:
            raise CheckpointUnavailable("Unexpected provider transaction response")
        # False = optimistic concurrency conflict / authorization change;
        # true is still provisional until persist_verified_round primary readback.
        return data["committed"]
