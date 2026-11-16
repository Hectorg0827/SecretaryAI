"""Independent, fail-closed capability match for regulated compliance writes.

PolicyEngine chooses the first matching tenant rule. A generic COMMIT allow or
one without an explicit API/capability match is not suitable permission to
record a regulatory source review, preparation or external reference.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException

from app.api.compliance_integrity import _audit_blocked_write

log = logging.getLogger(__name__)


def require_scoped_regulated_allow(
    db, *, company_id: str, actor: str, rule_id: str | None,
    capability: str, entity_type: str, entity_id: str,
) -> str:
    """Recheck the matching allow rule under tenant scope before RPC.

    Audit denials; do not leak policy rows, company identities or source values.
    The database also revalidates this rule when the atomic audit is inserted.
    """
    record = {"id": entity_id, "company_id": company_id}
    try:
        policy_id = str(UUID(str(rule_id)))
    except (ValueError, TypeError, AttributeError):
        _audit_blocked_write(
            db, table=entity_type, record=record,
            actor=actor, effect="deny", rule_id=None,
        )
        raise HTTPException(
            status_code=403,
            detail="An explicit scoped tenant policy allow is required",
        )

    try:
        query = (
            db.table("policy_rules")
            .select("id,company_id,is_active,action_class,effect,capabilities_match,paths_match")
            .eq("company_id", company_id)
            .eq("id", policy_id)
            .limit(2)
            .execute()
        )
        rows = getattr(query, "data", None)
    except Exception as exc:
        log.warning("Regulated policy scope recheck unavailable")
        _audit_blocked_write(
            db, table=entity_type, record=record,
            actor=actor, effect="deny", rule_id=policy_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Regulated tenant policy could not be confirmed",
        ) from exc

    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        log.warning("Regulated policy scope recheck malformed")
        _audit_blocked_write(
            db, table=entity_type, record=record,
            actor=actor, effect="deny", rule_id=policy_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Regulated tenant policy source invalid",
        )

    verified = (
        len(rows) == 1
        and str(rows[0].get("id")) == policy_id
        and str(rows[0].get("company_id")) == company_id
        and rows[0].get("is_active") is True
        and rows[0].get("action_class") == "commit"
        and rows[0].get("effect") == "allow"
        and isinstance(rows[0].get("capabilities_match"), list)
        and capability in rows[0]["capabilities_match"]
        and isinstance(rows[0].get("paths_match"), list)
        and "api" in rows[0]["paths_match"]
    )
    if not verified:
        _audit_blocked_write(
            db, table=entity_type, record=record,
            actor=actor, effect="deny", rule_id=policy_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Tenant policy rule does not explicitly allow this regulated API capability",
        )
    return policy_id
