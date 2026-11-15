"""Trusted cross-channel identity and consent resolution for Lola.

This is an internal, read-only integration seam. It does not create provider
mappings, consent grants, contact links or user-facing endpoints. Missing
backend-managed registry tables fail closed; a text or name similarity is
NEVER an acceptable proof of contact identity.

Only a privileged connector ingestion path may later populate verified rows.
An employee's own consent does not itself verify provider identity or link a
WhatsApp number to an email contact.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from app.intelligence.lola.continuity import Channel, WorkScope

GRANTS = "lola_channel_authorizations"
LINKS = "lola_verified_case_links"
_ALLOWED_ROLES = frozenset({"owner", "manager", "back_office", "sales_rep"})
_CHANNELS = frozenset({Channel.EMAIL.value, Channel.WHATSAPP.value})
GRANT_FIELDS = (
    "company_id,employee_id,channel,provider_identity_ref,"
    "provider_verification_state,connector_authorization_state,"
    "employee_consent_at,employee_consent_revoked_at,"
    "provider_verified_at,authorization_expires_at,source_revision"
)
LINK_FIELDS = (
    "company_id,employee_id,contact_id,work_item_id,"
    "email_provider_contact_ref,whatsapp_provider_contact_ref,"
    "link_verification_state,verification_source,"
    "employee_confirmed_at,link_verified_at,link_revoked_at,"
    "verification_expires_at,mapping_revision"
)


class IdentitySourceUnavailable(RuntimeError):
    """The registry could not be read or its contents could not be trusted."""


class IdentityAccessDenied(PermissionError):
    """The actor is not an active employee with this scope of access."""


class IdentityNotVerified(LookupError):
    """No currently authorized, independently verified mapping exists."""


@dataclass(frozen=True)
class ResolvedCrossChannelCase:
    scope: WorkScope
    email_provider_identity_ref: str
    whatsapp_provider_identity_ref: str
    email_contact_ref: str
    whatsapp_contact_ref: str
    source_fingerprint: str
    source: str = "backend_verified_registry"
    external_writes_executed: bool = False


def _nonempty(value: object, label: str, *, maxlen: int = 200) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > maxlen or any(ord(c) < 32 for c in value)):
        raise IdentitySourceUnavailable(f"Registry {label} malformed")
    return value


def _when(value: object, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 48:
        raise IdentitySourceUnavailable(f"Registry {label} timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise IdentitySourceUnavailable(f"Registry {label} timestamp invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IdentitySourceUnavailable(f"Registry {label} missing timezone")
    return parsed.astimezone(timezone.utc)


def _source_rows(db, table_name: str, columns: str, filters: tuple[tuple[str, str], ...],
                 limit: int, company: str) -> list[dict]:
    # Construction, query and execution may ALL fail (missing migration,
    # invalid table privileges, broken provider client). Never expose errors.
    try:
        query = db.table(table_name).select(columns)
        for field, value in filters:
            query = query.eq(field, value)
        result = query.limit(limit).execute()
    except Exception:
        raise IdentitySourceUnavailable("Trusted registry source unavailable") from None
    data = getattr(result, "data", None)
    if not isinstance(data, list) or any(
        not isinstance(row, dict) or str(row.get("company_id")) != company for row in data
    ):
        raise IdentitySourceUnavailable("Trusted registry returned unscoped data")
    return data


def _actor(user: dict, db) -> tuple[str, str]:
    """Current-active employee validation, not JWT role trust alone."""
    if not isinstance(user, dict):
        raise IdentityAccessDenied("Active employee required")
    company = user.get("company_id")
    employee = user.get("sub")
    role = user.get("role")
    if (not isinstance(company, str) or not company
            or not isinstance(employee, str) or not employee
            or role not in _ALLOWED_ROLES):
        raise IdentityAccessDenied("Employee not permitted to read continuity")
    try:
        result = (db.table("users")
                  .select("id,company_id,role,is_active")
                  .eq("id", employee).eq("company_id", company)
                  .limit(2).execute())
    except Exception:
        raise IdentitySourceUnavailable("Current employee membership unavailable") from None
    rows = getattr(result, "data", None)
    if (not isinstance(rows, list) or len(rows) != 1
            or not isinstance(rows[0], dict)
            or str(rows[0].get("id")) != employee
            or str(rows[0].get("company_id")) != company
            or rows[0].get("role") != role
            or rows[0].get("is_active") is not True):
        raise IdentityAccessDenied("Current employee membership not authorized")
    return company, employee


def resolve_verified_case(
    *,
    user: dict,
    db,
    contact_id: str,
    work_item_id: str,
    as_of: datetime,
) -> ResolvedCrossChannelCase:
    """Resolve a registry-certified case; never accepts a browser proof flag.

    This returns a source-backed WorkScope for the separate Stage 4 engine.
    Selectors can be user-supplied opaque IDs, but do not authorize any data
    unless exact company, employee and verified state match in the backend.
    """
    company, employee = _actor(user, db)
    contact_id = _nonempty(contact_id, "contact ID")
    work_item_id = _nonempty(work_item_id, "work item ID")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("An aware evaluation time is required")
    now = as_of.astimezone(timezone.utc)
    grants = _source_rows(
        db, GRANTS, GRANT_FIELDS,
        (("company_id", company), ("employee_id", employee)),
        3, company,
    )
    if len(grants) != 2 or any(
        not isinstance(row.get("channel"), str) for row in grants
    ):
        raise IdentityNotVerified("Both employee-authorized provider connections are required")
    if {row["channel"] for row in grants} != _CHANNELS:
        raise IdentityNotVerified("Both employee-authorized provider connections are required")
    by_channel = {row["channel"]: row for row in grants}
    provider_refs: dict[str, str] = {}
    for channel in _CHANNELS:
        row = by_channel[channel]
        if str(row.get("employee_id")) != employee:
            raise IdentitySourceUnavailable("Wrong employee in provider authorization")
        if (row.get("provider_verification_state") != "verified"
                or row.get("connector_authorization_state") != "connected"
                or row.get("employee_consent_revoked_at") is not None):
            raise IdentityNotVerified("Provider verification or employee consent revoked")
        if not row.get("employee_consent_at") or not row.get("provider_verified_at"):
            raise IdentityNotVerified("Independent provider proof and explicit consent required")
        consent_at = _when(row["employee_consent_at"], "employee consent")
        verified_at = _when(row["provider_verified_at"], "provider verification")
        if consent_at > now or verified_at > now:
            raise IdentityNotVerified("Provider verification or consent has a future timestamp")
        expires = row.get("authorization_expires_at")
        if expires is None:
            raise IdentityNotVerified("Provider authorization expiry must be verified")
        if _when(expires, "authorization expiry") <= now:
            raise IdentityNotVerified("Provider authorization expired")
        provider_refs[channel] = _nonempty(
            row.get("provider_identity_ref"), "provider identity",
        )
        _nonempty(row.get("source_revision"), "provider authorization revision")

    links = _source_rows(
        db, LINKS, LINK_FIELDS,
        (("company_id", company), ("employee_id", employee),
         ("contact_id", contact_id), ("work_item_id", work_item_id)),
        2, company,
    )
    if len(links) != 1:
        raise IdentityNotVerified("One independently verified contact/work-item link is required")
    link = links[0]
    if (link.get("employee_id") != employee
            or link.get("contact_id") != contact_id
            or link.get("work_item_id") != work_item_id):
        raise IdentitySourceUnavailable("Ambiguous or unscoped work-item mapping")
    if (link.get("link_verification_state") != "verified"
            or link.get("verification_source") != "provider_plus_employee"
            or link.get("link_revoked_at") is not None):
        raise IdentityNotVerified("Provider- and employee-verified mapping is required")
    if not link.get("employee_confirmed_at") or not link.get("link_verified_at"):
        raise IdentityNotVerified("Mapping lacks employee confirmation or independent verification")
    if (_when(link["employee_confirmed_at"], "mapping confirmation") > now
            or _when(link["link_verified_at"], "mapping verification") > now):
        raise IdentityNotVerified("Mapping verification is future dated")
    expires = link.get("verification_expires_at")
    if expires is None:
        raise IdentityNotVerified("Work-item verification expiry must be recorded")
    if _when(expires, "mapping expiry") <= now:
        raise IdentityNotVerified("Work-item mapping has expired")
    email_contact = _nonempty(link.get("email_provider_contact_ref"), "email contact")
    whatsapp_contact = _nonempty(link.get("whatsapp_provider_contact_ref"), "WhatsApp contact")
    mapping_revision = _nonempty(link.get("mapping_revision"), "mapping revision")
    grant_revisions = sorted(
        f"{key}:{_nonempty(row.get('source_revision'), 'authorization revision')}"
        for key, row in by_channel.items()
    )
    combined_revision = hashlib.sha256(
        "\n".join((company, employee, contact_id, work_item_id,
                   mapping_revision, *grant_revisions)).encode("utf-8")
    ).hexdigest()
    return ResolvedCrossChannelCase(
        scope=WorkScope(
            company_id=company, employee_id=employee,
            contact_id=contact_id, work_item_id=work_item_id,
            mapping_revision=combined_revision,
            employee_consent_verified=True,
        ),
        email_provider_identity_ref=provider_refs[Channel.EMAIL.value],
        whatsapp_provider_identity_ref=provider_refs[Channel.WHATSAPP.value],
        email_contact_ref=email_contact,
        whatsapp_contact_ref=whatsapp_contact,
        source_fingerprint=combined_revision,
    )
