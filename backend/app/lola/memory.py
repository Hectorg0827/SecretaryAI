"""
Lola unified memory — preferences, business context, decisions, commitments
and workflows, isolated by company, user, scope and sensitivity.

Isolation rules (enforced on write AND re-checked on every recall):

* Every query is filtered by ``company_id``. Memory never crosses tenants.
* ``scope='user'``       → only the owner can recall it.
* ``scope='team'``       → the owner, their managers (reporting chain upward,
                            i.e. anyone whose reporting tree contains the owner)
                            and executives.
* ``scope='company'``    → anyone in the company, but ONLY if sensitivity is
                            public/internal. Writing company scope requires an
                            executive.
* Confidential/restricted memories are forced to ``scope='user'`` so they can
  never leak into broader company memory.
* A memory linked to a customer account is recalled only while the caller can
  still see that account — reassigning the account hides the memory
  immediately (no stale-permission leak).
* A memory is never recalled above the caller's current sensitivity ceiling.
* Memory content is always handed to the model as UNTRUSTED data.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.lola.authorization import Authorizer
from app.lola.contracts import SENSITIVITY_RANK, Sensitivity

log = logging.getLogger(__name__)

MEMORY_KINDS = ("preference", "business_context", "decision", "commitment", "workflow")
MEMORY_SCOPES = ("user", "team", "company")
MAX_CONTENT = 1000
DEFAULT_RECALL_LIMIT = 12

_RESTRICTED = re.compile(
    r"\b(salar(y|ies)|payroll|wages?|ssn|social security|bank account|routing number|"
    r"medical|disciplinary|termination|home address|date of birth|passport)\b", re.IGNORECASE)
_CONFIDENTIAL = re.compile(
    r"\b(margin|cost price|landed cost|credit limit|owes|overdue|balance|discount|"
    r"profit|revenue target|commission)\b", re.IGNORECASE)
_SECRETS = re.compile(r"\b(password|api[_ ]?key|secret|token|pin code)\b\s*[:=]", re.IGNORECASE)


class MemoryUnavailable(RuntimeError):
    pass


class MemoryRejected(ValueError):
    pass


def classify_sensitivity(text: str, floor: Sensitivity = Sensitivity.INTERNAL) -> Sensitivity:
    level = floor
    if _CONFIDENTIAL.search(text) and SENSITIVITY_RANK[level] < SENSITIVITY_RANK[Sensitivity.CONFIDENTIAL]:
        level = Sensitivity.CONFIDENTIAL
    if _RESTRICTED.search(text):
        level = Sensitivity.RESTRICTED
    return level


def classify_kind(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(prefer|i like|always|never|call me|format)\b", low):
        return "preference"
    if re.search(r"\b(we decided|decision|agreed)\b", low):
        return "decision"
    if re.search(r"\b(promised|will send|committed|by (monday|tuesday|wednesday|thursday|friday|tomorrow))\b", low):
        return "commitment"
    if re.search(r"\b(every (week|month|day)|process|workflow|steps?)\b", low):
        return "workflow"
    return "business_context"


class LolaMemoryStore:
    def __init__(self, db, authorizer: Authorizer):
        self._db = db
        self._auth = authorizer
        self.p = authorizer.p

    # ── write ─────────────────────────────────────────────────────────────

    def remember(
        self,
        content: str,
        *,
        scope: str = "user",
        kind: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        sensitivity_floor: Sensitivity = Sensitivity.INTERNAL,
        conversation_id: Optional[str] = None,
        ttl_days: Optional[int] = None,
    ) -> dict:
        content = (content or "").strip()
        if not content:
            raise MemoryRejected("Nothing to remember")
        if len(content) > MAX_CONTENT:
            raise MemoryRejected("Memory is too long")
        if _SECRETS.search(content):
            raise MemoryRejected("Lola does not store passwords, keys or other secrets")
        if scope not in MEMORY_SCOPES:
            raise MemoryRejected("Unknown memory scope")
        kind = kind if kind in MEMORY_KINDS else classify_kind(content)
        sensitivity = classify_sensitivity(content, sensitivity_floor)

        if sensitivity == Sensitivity.RESTRICTED and self._auth.sensitivity_ceiling() != Sensitivity.RESTRICTED:
            raise MemoryRejected("Lola does not store HR or personal information for your role")
        # Sensitive memory is personal, always.
        if SENSITIVITY_RANK[sensitivity] >= SENSITIVITY_RANK[Sensitivity.CONFIDENTIAL]:
            scope = "user"
        if scope == "company" and not self.p.is_executive:
            raise MemoryRejected("Only leadership can save company-wide memory")
        if entity_type == "customer" and entity_id and not self._auth.can_see_account(entity_id):
            raise MemoryRejected("You cannot attach memory to that account")

        now = datetime.now(timezone.utc)
        row = {
            "id": str(uuid.uuid4()),
            "company_id": self.p.company_id,
            "owner_user_id": self.p.user_id,
            "scope": scope,
            "kind": kind,
            "sensitivity": sensitivity.value,
            "department": self.p.department.value,
            "content": content,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "source_conversation_id": conversation_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=ttl_days)).isoformat() if ttl_days else None,
        }
        try:
            self._db.table("lola_memories").insert(row).execute()
        except Exception as exc:
            raise MemoryUnavailable("Memory store unavailable") from exc
        return row

    def forget(self, memory_id: str) -> bool:
        """Only the owner can delete a memory (soft delete)."""
        try:
            res = (self._db.table("lola_memories")
                   .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
                   .eq("id", memory_id).eq("company_id", self.p.company_id)
                   .eq("owner_user_id", self.p.user_id).is_("deleted_at", "null").execute())
        except Exception as exc:
            raise MemoryUnavailable("Memory store unavailable") from exc
        return bool(getattr(res, "data", None))

    # ── read ──────────────────────────────────────────────────────────────

    def _visible(self, row: dict, now: datetime) -> bool:
        if str(row.get("company_id")) != str(self.p.company_id):
            return False
        if row.get("deleted_at"):
            return False
        exp = row.get("expires_at")
        if exp:
            try:
                if datetime.fromisoformat(str(exp).replace("Z", "+00:00")) <= now:
                    return False
            except ValueError:
                return False
        try:
            sens = Sensitivity(row.get("sensitivity"))
        except ValueError:
            return False
        owner = str(row.get("owner_user_id") or "")
        scope = row.get("scope")
        is_owner = owner == self.p.user_id
        rank = SENSITIVITY_RANK[sens]

        # 1. Scope: who may see it at all.
        if scope == "user":
            in_scope = is_owner
        elif scope == "team":
            in_scope = is_owner or owner in self.p.reports or self.p.is_executive
        elif scope == "company":
            in_scope = True
        else:
            in_scope = False
        if not in_scope:
            return False

        # 2. Sensitivity: anything above "internal" is personal to its owner
        #    (writes force this, recall re-checks it in case of bad rows).
        if rank > SENSITIVITY_RANK[Sensitivity.INTERNAL] and not is_owner:
            return False
        # 3. Restricted (HR/personal) needs CURRENT restricted clearance, even
        #    for its own author — e.g. someone who moved out of HR.
        if sens == Sensitivity.RESTRICTED and self._auth.sensitivity_ceiling() != Sensitivity.RESTRICTED:
            return False

        # 4. Linked records: the caller must still see the account right now.
        if row.get("entity_type") == "customer" and row.get("entity_id"):
            if not self._auth.can_see_account(str(row["entity_id"])):
                return False
        return True

    def recall(self, *, entity_ids: Optional[list[str]] = None, limit: int = DEFAULT_RECALL_LIMIT) -> list[dict]:
        try:
            rows = (self._db.table("lola_memories").select("*")
                    .eq("company_id", self.p.company_id).is_("deleted_at", "null")
                    .order("created_at", desc=True).limit(200).execute()).data or []
        except Exception as exc:
            raise MemoryUnavailable("Memory store unavailable") from exc
        now = datetime.now(timezone.utc)
        out = [r for r in rows if isinstance(r, dict) and self._visible(r, now)]
        if entity_ids:
            wanted = {str(e) for e in entity_ids}
            out.sort(key=lambda r: 0 if str(r.get("entity_id") or "") in wanted else 1)
        return out[:limit]

    @staticmethod
    def public_view(row: dict) -> dict[str, Any]:
        keys = ("id", "scope", "kind", "sensitivity", "content", "entity_type", "entity_id",
                "created_at", "expires_at")
        return {k: row.get(k) for k in keys}
