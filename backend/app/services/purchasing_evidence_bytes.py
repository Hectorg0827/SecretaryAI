"""Quarantined receiving-evidence ingestion.

Only the backend can access the private storage bucket. Never return a
download URL or claim malware safety/accounting validity from a hash match.
The storage object is written once (upsert disabled) and SQL retains an
append-only integrity attestation separately from the original claim.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

EVIDENCE_BUCKET = "purchasing-evidence-quarantine"
MAX_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 256 * 1024
_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,400}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class InspectedEvidence:
    data: bytes
    digest: str
    size: int
    mime: str


def _detect_mime(payload: bytes) -> str:
    """Signature gate, NOT antivirus scanning or full document validation."""
    if payload.startswith(b"%PDF-"):
        return "application/pdf"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise HTTPException(status_code=415, detail="Unsupported evidence file signature")


def _extension_matches(path: str, mime: str) -> bool:
    return (
        (mime == "application/pdf" and path.endswith(".pdf"))
        or (mime == "image/png" and path.endswith(".png"))
        or (mime == "image/jpeg" and path.endswith((".jpg", ".jpeg")))
    )


async def inspect_uploaded_evidence(
    file: UploadFile,
    *, expected_sha256: str,
    storage_key: str,
    company_id: str,
) -> InspectedEvidence:
    """Bound memory before hashing and before any storage or database write."""
    prefix = str(company_id) + "/purchasing/"
    if not (
        isinstance(storage_key, str)
        and storage_key.startswith(prefix)
        and _PATH_RE.fullmatch(storage_key)
        and ".." not in storage_key
        and "//" not in storage_key
    ):
        raise HTTPException(status_code=422, detail="Invalid tenant evidence path")
    if not isinstance(expected_sha256, str) or not _DIGEST_RE.fullmatch(expected_sha256):
        raise HTTPException(status_code=503, detail="Invalid stored evidence digest")

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    try:
        while True:
            piece = await file.read(_CHUNK_BYTES)
            if not piece:
                break
            size += len(piece)
            if size > MAX_BYTES:
                raise HTTPException(status_code=413, detail="Evidence exceeds 8 MiB limit")
            chunks.append(piece)
            digest.update(piece)
    finally:
        await file.close()
    if not size:
        raise HTTPException(status_code=422, detail="Evidence file is empty")
    actual_digest = digest.hexdigest()
    if not hmac.compare_digest(actual_digest, expected_sha256):
        raise HTTPException(status_code=422, detail="Evidence SHA-256 mismatch")
    payload = b"".join(chunks)
    mime = _detect_mime(payload)
    if not _extension_matches(storage_key, mime):
        raise HTTPException(status_code=422, detail="Evidence extension does not match signature")
    return InspectedEvidence(payload, actual_digest, size, mime)


async def store_quarantined_evidence(db, key: str, evidence: InspectedEvidence) -> None:
    """Require an existing *private* bucket; never create or expose it here."""
    try:
        bucket = await run_in_threadpool(db.storage.get_bucket, EVIDENCE_BUCKET)
    except Exception as exc:
        log.warning("Evidence bucket verification unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Private evidence storage unavailable") from exc
    public = bucket.get("public") if isinstance(bucket, dict) else getattr(bucket, "public", None)
    if public is not False:
        raise HTTPException(status_code=503, detail="Private evidence storage not configured")

    # The SDK upload is deliberately create-only. A collision cannot silently
    # replace a document; admins must resolve orphaned/duplicate object keys.
    try:
        await run_in_threadpool(
            lambda: db.storage.from_(EVIDENCE_BUCKET).upload(
                path=key,
                file=evidence.data,
                file_options={"content-type": evidence.mime, "upsert": "false"},
            )
        )
    except Exception as exc:
        log.warning("Evidence quarantine upload failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Evidence upload unavailable or object already exists") from exc


def valid_evidence_id(value: str) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False
