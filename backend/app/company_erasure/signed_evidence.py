"""Signed evidence verification for erasure REVIEW ONLY (not authorization).

No network, DB, secret keys, credential minting, external archive writing, or
delete path is exposed here. Runtime trust roots must be provisioned separately,
outside the request and outside the customer/tenant database. Empty or invalid
roots fail closed.

Each issuer signs its full dataclass claim AND a request/company identifier
under a versioned, domain-separated Ed25519 message. Signatures demonstrate
which configured issuer attested to claims; they do NOT prove jurisdictional
legal sufficiency, actual immutable archive storage or current IdP status.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .preflight import (
    ErasureRequest, LegalEvidence, ErasureApproval, ScopeReview,
    ExternalAuditReceipt, ReviewPacket, ReviewAssessment,
    evaluate_erasure_packet,
)

# The independent trust domains must not share a public key, even when given
# different key IDs. That would invalidate independent issuer separation.
_KIND_DOMAIN = {
    "request": "case",
    "retention": "legal",
    "legal_hold": "legal",
    "approval": "workforce",
    "scope": "inventory",
    "external_receipt": "independent_archive",
}
_KIND_RECORD = {
    "request": ErasureRequest,
    "retention": LegalEvidence,
    "legal_hold": LegalEvidence,
    "approval": ErasureApproval,
    "scope": ScopeReview,
    "external_receipt": ExternalAuditReceipt,
}
_REQUIRED_KINDS = frozenset(_KIND_RECORD)
_EVIDENCE_MAX_AGE = timedelta(hours=24)
_MAX_RECORD_SIZE = 16_384
_PREFIX = b"lionbi-erasure-review-ed25519-v1\n"


@dataclass(frozen=True)
class SignedEvidence:
    kind: str
    key_id: str
    request_id: str
    company_id: str
    issued_at: datetime
    record: object
    signature_b64: str


@dataclass(frozen=True)
class SignedReviewPacket:
    request: SignedEvidence
    retention: SignedEvidence
    legal_hold: SignedEvidence
    approvals: tuple[SignedEvidence, ...]
    scope: SignedEvidence
    external_receipt: SignedEvidence


def _blocked(code: str) -> ReviewAssessment:
    return ReviewAssessment("BLOCKED", (code,))


def _aware(dt: Any) -> bool:
    return (isinstance(dt, datetime) and dt.tzinfo is not None
            and dt.utcoffset() is not None)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        if not _aware(value):
            raise ValueError("Naive timestamp")
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("Nonstring key")
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if type(value) in (bool, str, int) or value is None:
        return value
    raise ValueError("Unsupported evidence value type")


def _signing_message(evidence: SignedEvidence) -> bytes:
    """Deterministic, versioned bytes. Only synthetic tests sign these bytes.

    App code must never contain an issuer private key or signing endpoint.
    """
    kind = evidence.kind
    if kind not in _KIND_RECORD or type(evidence.record) is not _KIND_RECORD[kind]:
        raise ValueError("Unexpected evidence kind or record type")
    if (not isinstance(evidence.key_id, str) or
        not isinstance(evidence.request_id, str) or
        not isinstance(evidence.company_id, str) or
        not _aware(evidence.issued_at)):
        raise ValueError("Incomplete evidence context")

    body = {
        "version": 1,
        "kind": kind,
        "key_id": evidence.key_id,
        "request_id": evidence.request_id,
        "company_id": evidence.company_id,
        "issued_at": evidence.issued_at,
        "record": asdict(evidence.record),
    }
    serialized = json.dumps(
        _canonical(body), sort_keys=True,
        separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    if len(serialized) > _MAX_RECORD_SIZE:
        raise ValueError("Evidence document oversized")
    return _PREFIX + serialized


def _verify_trust_roots(
    roots: Mapping[str, Mapping[str, bytes]],
) -> dict[str, dict[str, Ed25519PublicKey]]:
    """Check server-provided public keys (never fetch from signed payload).

    Five independent issuer families are required. A single trust root reused
    across legal/identity/archive domains is explicitly refused.
    """
    if not isinstance(roots, Mapping) or set(roots) != _REQUIRED_KINDS:
        raise ValueError("Trusted issuers missing or unknown")
    verified: dict[str, dict[str, Ed25519PublicKey]] = {}
    domain_by_raw_key: dict[bytes, str] = {}
    for kind, key_map in roots.items():
        if not isinstance(key_map, Mapping) or not key_map:
            raise ValueError("Issuer key list empty")
        verified[kind] = {}
        for kid, raw in key_map.items():
            if not isinstance(kid, str) or not kid or len(kid) > 100:
                raise ValueError("Invalid issuer key identifier")
            if not isinstance(raw, bytes) or len(raw) != 32:
                raise ValueError("Issuer public key must be 32 bytes")
            owner_domain = _KIND_DOMAIN[kind]
            if (raw in domain_by_raw_key and
                domain_by_raw_key[raw] != owner_domain):
                raise ValueError("Issuer trust domains reuse one key")
            domain_by_raw_key[raw] = owner_domain
            verified[kind][kid] = Ed25519PublicKey.from_public_bytes(raw)
    return verified


def _verify_record(
    evidence: SignedEvidence, *,
    kind: str, request_id: str, company_id: str,
    submitted_at: datetime, now: datetime,
    trusted: dict[str, dict[str, Ed25519PublicKey]],
) -> object:
    if not isinstance(evidence, SignedEvidence) or evidence.kind != kind:
        raise ValueError("Unexpected evidence kind")
    if evidence.request_id != request_id or evidence.company_id != company_id:
        raise ValueError("Evidence request/tenant mismatch")
    if (not _aware(evidence.issued_at) or
        not (submitted_at <= evidence.issued_at <= now) or
        now - evidence.issued_at > _EVIDENCE_MAX_AGE):
        raise ValueError("Evidence issued outside request window")
    if not isinstance(evidence.signature_b64, str):
        raise ValueError("Signature missing")
    if evidence.key_id not in trusted[kind]:
        raise ValueError("Unknown issuer key")
    signature = base64.b64decode(
        evidence.signature_b64, validate=True,
    )
    if len(signature) != 64:
        raise ValueError("Invalid signature length")
    trusted[kind][evidence.key_id].verify(
        signature, _signing_message(evidence),
    )
    return evidence.record


def evaluate_signed_review_packet(
    packet: SignedReviewPacket, *,
    trusted_public_keys: Mapping[str, Mapping[str, bytes]],
    now: datetime,
) -> ReviewAssessment:
    """Verify all independently signed evidence, then run review-only gate.

    This is deliberately not a privileged authorization API. Any absent key,
    forged signature, reused cross-domain trust key, swapped tenant/request,
    stale attestation or malformed input returns BLOCKED. Even valid evidence
    can only reach REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED.
    """
    if not _aware(now):
        return _blocked("untrusted_review_clock")
    if not isinstance(packet, SignedReviewPacket):
        return _blocked("malformed_signed_packet")
    try:
        trusted = _verify_trust_roots(trusted_public_keys)
        now = now.astimezone(timezone.utc)
        header = packet.request
        if not isinstance(header, SignedEvidence) or type(header.record) is not ErasureRequest:
            raise ValueError("Request evidence missing")
        req = header.record
        if not (_aware(req.submitted_at) and
                _aware(req.expires_at) and req.submitted_at <= now <
                req.expires_at):
            raise ValueError("Request expired or invalid")
        request_id = req.request_id
        company_id = req.company_id

        def verified(evidence: SignedEvidence, kind: str) -> object:
            return _verify_record(
                evidence, kind=kind, request_id=request_id,
                company_id=company_id, submitted_at=req.submitted_at,
                now=now, trusted=trusted,
            )

        verified(header, "request")
        retention = verified(packet.retention, "retention")
        hold = verified(packet.legal_hold, "legal_hold")
        if (not isinstance(packet.approvals, tuple)
                or not 2 <= len(packet.approvals) <= 10):
            raise ValueError("Unbounded approvals")
        approvals = tuple(verified(record, "approval") for record in packet.approvals)
        scope = verified(packet.scope, "scope")
        receipt = verified(packet.external_receipt, "external_receipt")
        return evaluate_erasure_packet(
            ReviewPacket(req, retention, hold, approvals, scope, receipt),
            now=now,
        )
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError,
            InvalidSignature, binascii.Error, UnicodeError, RecursionError):
        return _blocked("signed_evidence_unverified")
