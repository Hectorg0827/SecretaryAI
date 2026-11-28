"""Fail-closed persistence contract for regulated compliance records.

No source outage, malformed row, or unconfirmed write may be presented as success.
All operations are scoped to the authenticated tenant at their call sites.
"""
import logging

from fastapi import HTTPException

log = logging.getLogger(__name__)


def observed_rows(result) -> list[dict]:
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Compliance query did not return a validated row collection")
    return rows


def source_error(table: str, exc: Exception) -> HTTPException:
    log.error("Compliance source unavailable table=%s error_type=%s", table, type(exc).__name__)
    return HTTPException(status_code=503, detail="Compliance records are unavailable or invalid; no compliance determination was made")


def _audit_blocked_write(db, *, table: str, record: dict, actor: str,
                         effect: str, rule_id: str | None) -> None:
    """Durably record a policy rejection before returning the rejection.

    The generic audit helper is best-effort and is therefore not suitable here.
    Never record the full document or license number in an audit event.
    """
    import uuid

    event_id = str(uuid.uuid4())
    try:
        try:
            actor_uuid = str(uuid.UUID(actor))
        except (ValueError, TypeError, AttributeError):
            actor_uuid = None
        row = {
            "id": event_id,
            "company_id": record["company_id"],
            "user_id": actor_uuid,
            "event_type": "compliance.write_blocked",
            "action_class": "commit",
            "capability": "compliance",
            "path_used": "api",
            "entity_type": table,
            "entity_id": str(record["id"]),
            "description": "Compliance record write rejected for authenticated actor " + actor,
            "after_snapshot": {"submitted_fields": sorted(record.keys())},
            "policy_effect": effect,
            "policy_rule_id": rule_id,
        }
        result = db.table("audit_events").insert(row).execute()
        rows = observed_rows(result)
        if len(rows) != 1 or str(rows[0].get("id")) != event_id:
            raise ValueError("Compliance rejection audit not confirmed")
    except Exception as exc:
        log.error("Compliance rejection audit unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Compliance audit unavailable; no record changed") from exc


async def save_record(db, table: str, record: dict, *, user: dict) -> None:
    """Evaluate tenant policy, then atomically persist the record and audit.

    Every successful write uses the single-transaction DB function introduced in
    20261115000000_compliance_atomic_policy_audit.sql. No permissive fallback.
    """
    from app.domain.contracts import ActionClass, ActionProposal, DataPath
    from app.domain.policy import PolicyEngine

    allowed_tables = {
        "compliance_products", "compliance_licenses",
        "compliance_brand_registrations", "compliance_federal_permits",
        "compliance_distributors", "compliance_colas",
    }
    if table not in allowed_tables:
        raise HTTPException(status_code=403, detail="Unsupported compliance entity")

    actor = str(user.get("sub") or "")
    company_id = str(user.get("company_id") or "")
    if not actor or not company_id or str(record.get("company_id")) != company_id or not record.get("id"):
        raise HTTPException(status_code=403, detail="Compliance write identity invalid")

    proposal = ActionProposal(
        action_type="compliance.record_write",
        action_class=ActionClass.COMMIT,
        description="Record a regulated compliance entity",
        company_id=company_id,
        requested_by=actor,
        capability="compliance",
        path=DataPath.API,
        entity_type=table,
        entity_id=str(record["id"]),
        proposed_changes={"field_names": sorted(record.keys())},
    )
    try:
        decision = await PolicyEngine(company_id, db).evaluate(proposal)
    except Exception as exc:
        _audit_blocked_write(
            db, table=table, record=record, actor=actor,
            effect="deny", rule_id=None,
        )
        raise HTTPException(status_code=503, detail="Compliance policy unavailable; no record changed") from exc

    if decision.effect != "allow":
        _audit_blocked_write(
            db, table=table, record=record, actor=actor,
            effect=decision.effect, rule_id=decision.rule_id,
        )
        if decision.effect == "require_approval":
            raise HTTPException(
                status_code=409,
                detail="Compliance record change requires human approval; no record changed",
            )
        raise HTTPException(status_code=403, detail="Compliance policy denied record change")

    # The service-role API client bypasses RLS; check ownership before RPC.
    # The RPC repeats this check under a row lock to close concurrent races.
    try:
        existing = observed_rows(
            db.table(table).select("id,company_id").eq("id", record["id"])
            .limit(1).execute()
        )
        if any(str(row.get("company_id")) != company_id for row in existing):
            _audit_blocked_write(
                db, table=table, record=record, actor=actor,
                effect="deny", rule_id=decision.rule_id,
            )
            raise HTTPException(status_code=403, detail="Compliance record belongs to another company")
        result = db.rpc(
            "compliance_write_record_audited",
            {
                "p_table": table,
                "p_record": record,
                "p_actor": actor,
                "p_policy_effect": "allow",
                "p_policy_rule_id": decision.rule_id,
            },
        ).execute()
        receipt = getattr(result, "data", None)
        if not isinstance(receipt, dict):
            raise ValueError("Compliance atomic write receipt invalid")
        persisted = receipt.get("record")
        if (not isinstance(persisted, dict)
                or str(persisted.get("id")) != str(record["id"])
                or str(persisted.get("company_id")) != company_id
                or not isinstance(receipt.get("audit_event_id"), str)
                or not receipt["audit_event_id"]):
            raise ValueError("Compliance atomic record/audit receipt incomplete")
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Compliance atomic write unverified table=%s error_type=%s", table, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Compliance write or audit was not confirmed") from exc


def require_existing_record(db, table: str, record_id: str, company_id: str) -> None:
    """Forbid PUT from silently creating missing or foreign-company records."""
    try:
        rows = observed_rows(
            db.table(table).select("id").eq("id", record_id)
            .eq("company_id", company_id).limit(1).execute()
        )
    except Exception as exc:
        raise source_error(table, exc) from exc
    if len(rows) != 1 or str(rows[0].get("id")) != str(record_id):
        raise HTTPException(status_code=404, detail="Compliance record not found")


def delete_record(db, table: str, record_id: str, company_id: str) -> None:
    """Forbid destructive compliance deletion until an approved retention flow exists.

    Keep this shared guard even if an API route is renamed. A database update,
    archival workflow, or tenant-deletion exception must be independently
    designed, reviewed and audited before it can be implemented.
    """
    raise HTTPException(
        status_code=403,
        detail="Compliance records are retained. Deletion is prohibited.",
    )
