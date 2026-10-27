"""Pure, read-only mailbox event identity and account-candidate matching.

The caller MUST supply verified tenant, provider connection and mailbox scope.
No OAuth profile, mailbox identity, permission, sender authentication or durable
ingestion checkpoint is inferred by this module. An exact From-address match
is merely an unverified candidate, never authority for actions or access.

Chat 1 owns the future shared CommunicationEvent schema and persisted event
deduplication/checkpoint. These helpers intentionally do not write to a DB.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Collection, Mapping, Sequence


@dataclass(frozen=True)
class MailboxMessageIdentity:
    tenant_id: str
    source_connection_id: str
    provider: str
    mailbox_id: str
    provider_message_id: str
    event_key: str
    thread_key: str | None
    internet_message_id: str | None
    sender_email: str | None


@dataclass(frozen=True)
class AccountEmailContact:
    """An account contact supplied by a trusted tenant-scoped account read."""

    tenant_id: str
    account_id: str
    email: str


@dataclass(frozen=True)
class AccountCandidateMatch:
    status: str  # candidate, ambiguous, unmatched, invalid_sender, unavailable
    account_id: str | None = None
    evidence: str | None = None
    attribution_verified: bool = False


def _required(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Email {label} requires a nonempty string")
    return value.strip()


def _scoped_key(kind: str, *components: str) -> str:
    # JSON string boundaries prevent composite-key collisions even when source
    # IDs contain punctuation. Hashing avoids exposing raw IDs in stored keys.
    envelope = json.dumps([kind, *components], ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
    return f"{kind}-v1:{digest}"


def _sender_address(value: object) -> str | None:
    """Conservative, exact address parser; intentionally no domain matching."""
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n,;"):
        return None
    raw = value.strip()
    if not raw:
        return None
    _, parsed = parseaddr(raw)
    if not parsed or parsed.count("@") != 1:
        return None
    local, domain = parsed.rsplit("@", 1)
    if (not local or not domain or any(c.isspace() for c in parsed)
            or any(c in parsed for c in "<>\"\\") or domain.startswith(".")
            or domain.endswith(".") or ".." in domain):
        return None
    if raw != parsed:
        # Permit one conventional display-name wrapper, not address lists,
        # comments, or a bare email found in an arbitrary display string.
        if (raw.count("<") != 1 or raw.count(">") != 1
                or not raw.endswith(f"<{parsed}>")
                or "@" in raw.split("<", 1)[0]):
            return None
    return parsed.casefold()


def identify_mailbox_message(
    *, tenant_id: str, source_connection_id: str, provider: str,
    mailbox_id: str, message: Mapping[str, object],
) -> MailboxMessageIdentity:
    """Create a replay-stable provider message key without claiming persistence.

    Provider message IDs are opaque and case-sensitive. The Internet Message-ID
    is supplementary evidence, not the deduplication key: it can appear in
    multiple mailbox copies or be missing altogether.
    """
    tenant = _required(tenant_id, "tenant_id")
    connection = _required(source_connection_id, "source_connection_id")
    provider_name = _required(provider, "provider").casefold()
    mailbox = _required(mailbox_id, "mailbox_id")
    if provider_name not in {"gmail", "outlook"}:
        raise ValueError("Unsupported email provider")
    if not isinstance(message, Mapping):
        raise ValueError("Email message must be a mapping")
    provider_message_id = _required(message.get("id"), "source message id")

    thread_raw = message.get("thread_id")
    if thread_raw is not None and not isinstance(thread_raw, str):
        raise ValueError("Email thread_id must be a string or null")
    thread_id = thread_raw.strip() if thread_raw else None

    internet_raw = message.get("message_id")
    if internet_raw is not None and not isinstance(internet_raw, str):
        raise ValueError("Email internet message ID must be a string or null")
    internet_id = internet_raw.strip() if internet_raw else None

    # Graph supplies an explicit address separately from the sender display
    # name. Gmail includes the sender address in its From header.
    if provider_name == "outlook":
        sender_email = _sender_address(message.get("from_email"))
    else:
        sender_email = _sender_address(message.get("from_email") or message.get("from"))

    scope = (tenant, connection, provider_name, mailbox)
    return MailboxMessageIdentity(
        tenant_id=tenant,
        source_connection_id=connection,
        provider=provider_name,
        mailbox_id=mailbox,
        provider_message_id=provider_message_id,
        event_key=_scoped_key("email-event", *scope, provider_message_id),
        thread_key=_scoped_key("email-thread", *scope, thread_id) if thread_id else None,
        internet_message_id=internet_id,
        sender_email=sender_email,
    )


def match_sender_account_candidate(
    identity: MailboxMessageIdentity,
    contacts: Sequence[AccountEmailContact],
    *, permitted_account_ids: Collection[str] | None,
) -> AccountCandidateMatch:
    """Return an *unverified* candidate only for one exact permitted match.

    `permitted_account_ids` MUST be a server-derived tenant/role filter. A
    missing filter fails closed. A From header alone proves neither sender
    control nor direction, so even a unique match never verifies attribution.
    """
    if permitted_account_ids is None:
        return AccountCandidateMatch(status="unavailable")
    if isinstance(permitted_account_ids, (str, bytes)):
        raise ValueError("Account permission scope must be a collection of IDs")
    allowed = set(permitted_account_ids)
    if any(not isinstance(account_id, str) or not account_id for account_id in allowed):
        raise ValueError("Account permission scope contains invalid IDs")
    if identity.sender_email is None:
        return AccountCandidateMatch(status="invalid_sender")

    matches: set[str] = set()
    for contact in contacts:
        if not isinstance(contact, AccountEmailContact):
            raise ValueError("Invalid email contact record")
        contact_tenant = _required(contact.tenant_id, "contact tenant")
        account_id = _required(contact.account_id, "contact account id")
        email = _sender_address(contact.email)
        if not email:
            raise ValueError("Invalid email contact address")
        if contact_tenant == identity.tenant_id and account_id in allowed and email == identity.sender_email:
            matches.add(account_id)

    if len(matches) > 1:
        return AccountCandidateMatch(status="ambiguous")
    if not matches:
        return AccountCandidateMatch(status="unmatched")
    return AccountCandidateMatch(
        status="candidate", account_id=next(iter(matches)),
        evidence="sender_address_exact_match_unverified",
    )
