"""
SecretaryAI · Feature Factory — Audit Log (Layer 8)
===================================================
Records every privileged event (feature created, capabilities approved, run
executed, approval decided, kill switch toggled). Two properties make it
trustworthy:

  1. Append-only: the database itself blocks UPDATE/DELETE on ff_audit_log
     (see the trigger in 001_feature_factory.sql). The app cannot rewrite it.

  2. Hash-chained: each row stores the hash of the previous row plus its own
     content. If anyone edits an old row at the storage layer, every later
     hash stops matching — tampering becomes detectable, not silent.

This is what lets you answer "why did SecretaryAI do this on March 3rd?" with
a complete, verifiable trail.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional
from uuid import UUID

from .repository import DBExecutor


def _canonical(payload: dict[str, Any]) -> str:
    """Stable JSON so the same content always hashes the same way."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(prev_hash: Optional[str], payload: dict[str, Any]) -> str:
    base = (prev_hash or "") + _canonical(payload)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


class AuditLogger:
    def __init__(self, db: DBExecutor, tenant_id: UUID):
        self._db = db
        self._tenant_id = tenant_id

    async def record(
        self,
        event_type: str,
        *,
        actor_id: Optional[UUID],
        feature_id: Optional[UUID] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        detail = detail or {}

        # Fetch the most recent hash for this tenant to extend the chain.
        prev = await self._db.fetchrow(
            "SELECT hash FROM ff_audit_log WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1",
            self._tenant_id,
        )
        prev_hash = prev["hash"] if prev else None

        payload = {
            "tenant_id": str(self._tenant_id),
            "feature_id": str(feature_id) if feature_id else None,
            "actor_id": str(actor_id) if actor_id else None,
            "event_type": event_type,
            "detail": detail,
        }
        new_hash = _compute_hash(prev_hash, payload)

        await self._db.execute(
            """
            INSERT INTO ff_audit_log
              (tenant_id, feature_id, actor_id, event_type, detail, prev_hash, hash)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            self._tenant_id, feature_id, actor_id, event_type,
            json.dumps(detail), prev_hash, new_hash,
        )

    async def verify_chain(self) -> bool:
        """
        Re-walk the whole chain and confirm no row has been altered. Run this
        from an admin diagnostics page or a scheduled integrity check.
        """
        rows = await self._db.fetch(
            "SELECT * FROM ff_audit_log WHERE tenant_id = $1 ORDER BY created_at ASC",
            self._tenant_id,
        )
        prev_hash: Optional[str] = None
        for r in rows:
            payload = {
                "tenant_id": str(r["tenant_id"]),
                "feature_id": str(r["feature_id"]) if r["feature_id"] else None,
                "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
                "event_type": r["event_type"],
                "detail": r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"]),
            }
            expected = _compute_hash(prev_hash, payload)
            if expected != r["hash"]:
                return False
            prev_hash = r["hash"]
        return True
