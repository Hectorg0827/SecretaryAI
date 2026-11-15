"""Read-only compatibility check against EXISTING Lola private staging tables.

This is not the verified identity resolver. Existing contact flags and email
integration records do not by themselves establish a complete, authoritative
cross-channel conversation or consent to proactive messaging.

Queries are deliberately column-restricted: no OAuth tokens, message bodies,
contact email/phone or private event summaries are retrieved or returned.
"""
from __future__ import annotations

from uuid import UUID

from app.intelligence.lola.identity_registry import (
    IdentityAccessDenied, IdentitySourceUnavailable, _actor,
)

EMAIL = "employee_integrations"
WHATSAPP = "lola_employee_whatsapp_connections"
CONTACTS = "lola_private_contacts"


def _rows(db, table: str, fields: str, filters: tuple[tuple[str, str], ...],
          limit: int, company: str, actor: str) -> list[dict]:
    try:
        query = db.table(table).select(fields)
        for field, value in filters:
            query = query.eq(field, value)
        rows = query.limit(limit).execute().data
    except Exception:
        raise IdentitySourceUnavailable("Existing private source unavailable") from None
    if not isinstance(rows, list) or len(rows) > limit:
        raise IdentitySourceUnavailable("Private source page invalid")
    if any(
        not isinstance(row, dict)
        or str(row.get("company_id")) != company
        or str(row.get("user_id")) != actor for row in rows
    ):
        raise IdentitySourceUnavailable("Private source identity mismatch")
    return rows


def inspect_existing_readiness(*, user: dict, db, contact_id: UUID | None = None) -> dict:
    company, employee = _actor(user, db)
    filters = (("company_id", company), ("user_id", employee))

    emails = _rows(
        db, EMAIL, "company_id,user_id,provider,connected_at,updated_at",
        filters, 3, company, employee,
    )
    allowed = {"gmail", "outlook"}
    if any(
        row.get("provider") not in allowed for row in emails
    ) or len({row.get("provider") for row in emails}) != len(emails):
        raise IdentitySourceUnavailable("Ambiguous employee email integration")
    valid_emails = [
        row for row in emails
        if isinstance(row.get("connected_at"), str) and row["connected_at"].strip()
    ]
    email_state = (
        "single_connection_recorded" if len(valid_emails) == 1
        else "multiple_connections_require_selection" if len(valid_emails) > 1
        else "no_active_email_connection_recorded"
    )

    phones = _rows(
        db, WHATSAPP,
        "company_id,user_id,status,phone_number_id,waba_id,meta_verified_at,"
        "employee_consented_at,employee_revoked_at,history_sync_consented,"
        "policy_clearance_id,updated_at",
        filters, 2, company, employee,
    )
    if len(phones) > 1:
        raise IdentitySourceUnavailable("Multiple employee WhatsApp connection records")
    phone = phones[0] if phones else None
    whatsapp_active = bool(
        phone
        and phone.get("status") == "active"
        and isinstance(phone.get("phone_number_id"), str)
        and bool(phone["phone_number_id"].strip())
        and isinstance(phone.get("waba_id"), str)
        and bool(phone["waba_id"].strip())
        and phone.get("meta_verified_at")
        and phone.get("employee_consented_at")
        and phone.get("employee_revoked_at") is None
        and phone.get("history_sync_consented") is True
        and phone.get("policy_clearance_id")
    )
    whatsapp_state = (
        "existing_consent_and_ownership_metadata_present"
        if whatsapp_active else
        "connection_pending_or_consent_incomplete" if phone else
        "no_employee_whatsapp_connection_recorded"
    )

    contact_state = "not_checked"
    if contact_id is not None:
        if not isinstance(contact_id, UUID):
            raise ValueError("Contact ID must be an opaque UUID")
        matches = _rows(
            db, CONTACTS, "id,company_id,user_id,identity_verified",
            (*filters, ("id", str(contact_id))), 2, company, employee,
        )
        if len(matches) > 1:
            raise IdentitySourceUnavailable("Duplicate private contact IDs")
        if matches and str(matches[0].get("id")) != str(contact_id):
            raise IdentitySourceUnavailable("Private contact ID mismatch")
        contact_state = (
            "existing_identity_flag_recorded_not_provider_verified"
            if matches and matches[0].get("identity_verified") is True else
            "candidate_only_or_not_found"
        )

    # These existing tables lack a durable complete page cursor and separate
    # authenticated provider+employee work-item linkage evidence. The presence
    # of rows is not sufficient to claim identity verification or no-reply.
    return {
        "source": "existing_employee_private_tables",
        "email_state": email_state,
        "whatsapp_state": whatsapp_state,
        "contact_state": contact_state,
        "provider_message_coverage_verified": False,
        "cross_channel_work_item_verified": False,
        "eligible_for_follow_up_engine": False,
        "proactive_notifications_enabled_by_this_check": False,
        "external_writes_executed": False,
        "next_gate": (
            "Verify provider identity, employee-owned work-item mapping and "
            "durable complete channel checkpoints before evaluating silence."
        ),
    }
