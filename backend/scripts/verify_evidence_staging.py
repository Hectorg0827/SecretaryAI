#!/usr/bin/env python3
"""Opt-in real ClamAV and PRIVATE Supabase Storage staging acceptance probe.

Never runs against an unknown project. Requires an explicitly confirmed
isolated Supabase staging project and distinct staging-only credentials.
Storage probe creates two unique short-lived objects and deletes both.
It never changes the production DB, performs a DB migration or posts AP.

From backend/:
 EVIDENCE_STAGING_CONFIRM=I_CONFIRM_ISOLATED_STAGING \
 EVIDENCE_STAGING_PROJECT_REF=<staging-ref> \
 EVIDENCE_STAGING_SUPABASE_URL=https://<staging-ref>.supabase.co \
 EVIDENCE_STAGING_SERVICE_ROLE_KEY=<staging-key> \
 EVIDENCE_STAGING_ANON_KEY=<staging-anon> \
 CLAMAV_UNIX_SOCKET=/run/clamav/clamd.ctl \
 python scripts/verify_evidence_staging.py --scanner --storage-write-test
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.purchasing_evidence_bytes import EVIDENCE_BUCKET, MAX_BYTES
from app.services.purchasing_evidence_malware import _clamav_scan


class StagingProbeFailure(RuntimeError):
    pass


# EICAR is a harmless antivirus test string, not real malware.
_EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)
_CLEAN_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
_CONFIRM = "I_CONFIRM_ISOLATED_STAGING"
_REF_RE = re.compile(r"^[a-z0-9]{8,32}$")
_MAX_SIG_AGE = timedelta(days=7)


def _clamd_command(command: bytes, socket_path: str) -> bytes:
    if not socket_path.startswith("/") or "\x00" in socket_path:
        raise StagingProbeFailure("Local ClamAV Unix socket required")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(10)
            conn.connect(socket_path)
            conn.sendall(command)
            reply = bytearray()
            while len(reply) < 1024:
                chunk = conn.recv(min(256, 1024 - len(reply)))
                if not chunk:
                    break
                reply.extend(chunk)
                if b"\x00" in reply:
                    break
    except OSError as exc:
        raise StagingProbeFailure("Live ClamAV daemon unavailable") from exc
    if b"\x00" not in reply:
        raise StagingProbeFailure("Incomplete ClamAV daemon response")
    return bytes(reply).split(b"\x00", 1)[0]


def parse_clamd_signature_date(reply: bytes, *, now: datetime) -> datetime:
    """Check date of loaded AV database from real ClamAV VERSION response."""
    try:
        parts = reply.decode("ascii").split("/")
        if len(parts) != 3 or not parts[0].startswith("ClamAV "):
            raise ValueError("Invalid ClamAV version")
        if not parts[1].isdigit() or int(parts[1]) <= 0:
            raise ValueError("Invalid database version")
        recorded = datetime.strptime(parts[2], "%a %b %d %H:%M:%S %Y")
        recorded = recorded.replace(tzinfo=timezone.utc)
    except (UnicodeError, ValueError) as exc:
        raise StagingProbeFailure("ClamAV signature date unavailable") from exc
    if now.tzinfo is None:
        raise StagingProbeFailure("Reference clock must be timezone aware")
    age = now.astimezone(timezone.utc) - recorded
    if age > _MAX_SIG_AGE or age < -timedelta(hours=24):
        raise StagingProbeFailure("ClamAV signatures stale or staging clock incorrect")
    return recorded


def probe_real_scanner(socket_path: str) -> dict:
    if not socket_path:
        raise StagingProbeFailure("Set CLAMAV_UNIX_SOCKET")
    version = _clamd_command(b"zVERSION\x00", socket_path)
    signatures = parse_clamd_signature_date(version, now=datetime.now(timezone.utc))
    if _clamav_scan(_CLEAN_PDF, socket_path) != "clean":
        raise StagingProbeFailure("Clean PDF fixture unexpectedly infected")
    if _clamav_scan(_EICAR, socket_path) != "infected":
        raise StagingProbeFailure("Live scanner failed EICAR detection")
    return {"clean": "clean", "eicar": "infected", "signatures": signatures.isoformat()}


def staging_config(env: dict[str, str]) -> dict[str, str]:
    if env.get("EVIDENCE_STAGING_CONFIRM") != _CONFIRM:
        raise StagingProbeFailure("Explicit isolated staging confirmation required")
    ref = env.get("EVIDENCE_STAGING_PROJECT_REF", "")
    if not _REF_RE.fullmatch(ref):
        raise StagingProbeFailure("Expected staging Supabase project ref required")
    url = env.get("EVIDENCE_STAGING_SUPABASE_URL", "")
    p = urlparse(url)
    if (p.scheme != "https" or p.hostname != f"{ref}.supabase.co"
            or p.port not in (None, 443) or p.username or p.password
            or p.path not in ("", "/") or p.query or p.fragment):
        raise StagingProbeFailure("URL does not match confirmed staging Supabase project")
    key = env.get("EVIDENCE_STAGING_SERVICE_ROLE_KEY", "")
    anon = env.get("EVIDENCE_STAGING_ANON_KEY", "")
    if not key or not anon or key == anon:
        raise StagingProbeFailure("Separate staging service and anon credentials required")
    return {"url": url, "service": key, "anon": anon}


def bucket_field(bucket, field):
    return bucket.get(field) if isinstance(bucket, dict) else getattr(bucket, field, None)


def validate_bucket_limits(bucket) -> None:
    if bucket_field(bucket, "public") is not False:
        raise StagingProbeFailure("Evidence quarantine bucket is public")
    limit = bucket_field(bucket, "file_size_limit")
    if type(limit) is not int or not 0 < limit <= MAX_BYTES:
        raise StagingProbeFailure("Set evidence bucket file size limit to <=8 MiB")
    mimes = bucket_field(bucket, "allowed_mime_types")
    allowed = {"application/pdf", "image/jpeg", "image/png"}
    if not isinstance(mimes, list) or not mimes or not set(mimes).issubset(allowed):
        raise StagingProbeFailure("Restrict evidence bucket to PDF/JPEG/PNG MIME types")


def require_anon_denied(attempt, what: str):
    """Treat any unexpectedly successful anon action as a security failure."""
    try:
        response = attempt()
    except Exception:
        return
    if isinstance(response, dict) and response.get("error"):
        return
    raise StagingProbeFailure(f"Anonymous client can {what}")


def probe_real_private_storage(service, anon):
    validate_bucket_limits(service.storage.get_bucket(EVIDENCE_BUCKET))
    prefix = str(uuid4()) + "/purchasing/staging-probe/"
    clean_key = prefix + "fixture.pdf"
    attempted_key = prefix + "anon.pdf"
    keys = [clean_key, attempted_key]
    failure = None
    try:
        service.storage.from_(EVIDENCE_BUCKET).upload(
            path=clean_key, file=_CLEAN_PDF,
            file_options={"content-type": "application/pdf", "upsert": "false"},
        )
        loaded = service.storage.from_(EVIDENCE_BUCKET).download(clean_key)
        if not isinstance(loaded, bytes) or hashlib.sha256(loaded).digest() != hashlib.sha256(_CLEAN_PDF).digest():
            raise StagingProbeFailure("Staging service-role storage roundtrip corrupted bytes")
        require_anon_denied(
            lambda: anon.storage.from_(EVIDENCE_BUCKET).download(clean_key),
            "download quarantined evidence")
        require_anon_denied(
            lambda: anon.storage.from_(EVIDENCE_BUCKET).upload(
                path=attempted_key, file=_CLEAN_PDF,
                file_options={"content-type": "application/pdf", "upsert": "false"}),
            "write into private quarantine")
        require_anon_denied(
            lambda: anon.storage.from_(EVIDENCE_BUCKET).list(prefix),
            "list private quarantine objects")
    except Exception as exc:
        failure = exc
    finally:
        try:
            service.storage.from_(EVIDENCE_BUCKET).remove(keys)
            try:
                service.storage.from_(EVIDENCE_BUCKET).download(clean_key)
            except Exception:
                pass
            else:
                raise StagingProbeFailure("Temporary evidence was still readable after cleanup")
        except Exception as exc:
            raise StagingProbeFailure("Staging probe cleanup failed; investigate objects") from exc
    if failure is not None:
        if isinstance(failure, StagingProbeFailure):
            raise failure
        raise StagingProbeFailure("Real staging private-storage probe failed") from failure
    return {"private": True, "anon_read": "denied", "anon_write": "denied",
            "anon_list": "denied", "temporary_files": "removed"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Isolated staging evidence acceptance")
    parser.add_argument("--scanner", action="store_true")
    parser.add_argument("--storage-write-test", action="store_true")
    args = parser.parse_args(argv)
    if not (args.scanner or args.storage_write_test):
        parser.error("Select --scanner and/or --storage-write-test")
    try:
        config = staging_config(dict(os.environ))
        if args.scanner:
            status = probe_real_scanner(os.environ.get("CLAMAV_UNIX_SOCKET", ""))
            print("PASS real ClamAV clean/EICAR & fresh signatures", status["signatures"])
        if args.storage_write_test:
            from supabase import create_client
            service = create_client(config["url"], config["service"])
            anon = create_client(config["url"], config["anon"])
            probe_real_private_storage(service, anon)
            print("PASS staging private storage ACL and isolated temporary upload")
    except StagingProbeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
