"""
IngestionPipeline — hardened file ingestion with deduplication, retry, and
entity classification.

Backed by the `ingestion_queue` table introduced in Phase 6.

Deduplication strategy
──────────────────────
Each file is identified by (company_id, file_path, file_hash).  The hash is a
SHA-256 of the file's raw bytes.  If a file hasn't changed since the last
successful ingestion, its row will already be in status='done' with the same
hash — so re-scanning the directory produces no duplicate work.

If the file's content changes (new hash), the UNIQUE constraint on
(company_id, file_path, file_hash) is not violated because the hash differs;
the new version gets its own row.

Retry logic
───────────
On failure, attempt_count is incremented and next_retry_at is set to a
back-off time.  The scheduler only re-queues rows whose next_retry_at has
passed and whose attempt_count < max_attempts.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".pdf"}

# Entity type guessed from filename substrings (checked in order)
_ENTITY_HINTS: list[tuple[str, str]] = [
    ("customer",  "customers"),
    ("invoice",   "invoices"),
    ("inventory", "inventory"),
    ("item",      "inventory"),
    ("payment",   "payments"),
    ("vendor",    "vendors"),
    ("po",        "purchase_orders"),
    ("purchase",  "purchase_orders"),
]

# Retry back-off delays (seconds) indexed by attempt number
_RETRY_DELAYS = [60, 300, 900]


class IngestionPipeline:
    """
    Manages the ingestion_queue table for a single company.

    All write operations are non-fatal: they log warnings and return gracefully
    rather than raising, so callers are not disrupted by transient DB issues.
    """

    def __init__(self, db, company_id: str):
        self._db = db
        self._company_id = company_id

    # ── File discovery ────────────────────────────────────────────────────────

    @staticmethod
    def scan_directory(directory: str) -> list[dict]:
        """
        Walk `directory` and return metadata for each ingestible file.
        Skips hidden files, temp files, and unsupported extensions.
        Returns a list of dicts with keys: path, name, suffix, size.
        """
        p = Path(directory)
        if not p.is_dir():
            return []

        found = []
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if f.name.startswith(".") or f.name.startswith("~$"):
                continue
            if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                found.append({
                    "path": str(f),
                    "name": f.name,
                    "suffix": f.suffix.lower(),
                    "size": f.stat().st_size,
                })
            except OSError:
                continue
        return found

    @staticmethod
    def compute_hash(file_path: str) -> Optional[str]:
        """
        Return the SHA-256 hex digest of the file's contents.
        Returns None if the file cannot be read (permissions, deleted, etc.).
        """
        try:
            h = hashlib.sha256()
            with open(file_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError as exc:
            log.warning("IngestionPipeline: cannot hash %s: %s", file_path, exc)
            return None

    @staticmethod
    def classify_entity(file_name: str) -> str:
        """
        Guess the entity type from the filename.
        Returns one of: customers, invoices, inventory, payments, vendors,
        purchase_orders, or 'unknown'.
        """
        lower = file_name.lower()
        for hint, entity in _ENTITY_HINTS:
            if hint in lower:
                return entity
        return "unknown"

    # ── Queue management ──────────────────────────────────────────────────────

    def register_file(
        self,
        file_path: str,
        file_hash: str,
        *,
        file_size: Optional[int] = None,
        entity_type: Optional[str] = None,
    ) -> tuple[Optional[str], bool]:
        """
        Register a file in the ingestion queue.

        Returns (file_id, is_new):
          - is_new=True  → this (path, hash) combo had not been seen before
          - is_new=False → row already exists (deduplication hit); returns its id
          - file_id=None → DB error
        """
        if entity_type is None:
            entity_type = self.classify_entity(Path(file_path).name)

        # Check for existing row with same path + hash
        try:
            existing = (
                self._db.table("ingestion_queue")
                .select("id, status")
                .eq("company_id", self._company_id)
                .eq("file_path", file_path)
                .eq("file_hash", file_hash)
                .limit(1)
                .execute()
            )
            if existing.data:
                return existing.data[0]["id"], False
        except Exception as exc:
            log.warning("IngestionPipeline: register_file lookup failed: %s", exc)
            return None, False

        # Insert new row
        import uuid
        file_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._db.table("ingestion_queue").insert({
                "id": file_id,
                "company_id": self._company_id,
                "file_path": file_path,
                "file_hash": file_hash,
                "file_size": file_size,
                "entity_type": entity_type,
                "status": "pending",
                "attempt_count": 0,
                "first_seen_at": now,
            }).execute()
            log.debug("IngestionPipeline: registered %s (%s)", file_path, entity_type)
            return file_id, True
        except Exception as exc:
            log.warning("IngestionPipeline: register_file insert failed: %s", exc)
            return None, False

    def get_pending(self, limit: int = 10) -> list[dict]:
        """
        Return up to `limit` files that are ready to be processed.
        Includes:
          - status='pending'
          - status='error' with next_retry_at <= now AND attempt_count < max_attempts
        """
        now = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []

        try:
            result = (
                self._db.table("ingestion_queue")
                .select("*")
                .eq("company_id", self._company_id)
                .eq("status", "pending")
                .order("first_seen_at", desc=False)
                .limit(limit)
                .execute()
            )
            rows.extend(result.data or [])
        except Exception as exc:
            log.warning("IngestionPipeline: get_pending (pending) failed: %s", exc)

        if len(rows) < limit:
            try:
                retry_result = (
                    self._db.table("ingestion_queue")
                    .select("*")
                    .eq("company_id", self._company_id)
                    .eq("status", "error")
                    .lte("next_retry_at", now)
                    .order("next_retry_at", desc=False)
                    .limit(limit - len(rows))
                    .execute()
                )
                rows.extend(retry_result.data or [])
            except Exception as exc:
                log.warning("IngestionPipeline: get_pending (retry) failed: %s", exc)

        return rows

    def mark_processing(self, file_id: str) -> None:
        """Claim a file for processing."""
        try:
            self._db.table("ingestion_queue").update({
                "status": "processing",
                "last_processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", file_id).execute()
        except Exception as exc:
            log.warning("IngestionPipeline: mark_processing failed for %s: %s",
                        file_id, exc)

    def mark_done(self, file_id: str, rows_ingested: int) -> None:
        """Mark a file as successfully ingested."""
        try:
            self._db.table("ingestion_queue").update({
                "status": "done",
                "rows_ingested": rows_ingested,
                "error_detail": None,
                "last_processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", file_id).execute()
        except Exception as exc:
            log.warning("IngestionPipeline: mark_done failed for %s: %s",
                        file_id, exc)

    def mark_error(
        self,
        file_id: str,
        error: str,
        *,
        retry: bool = True,
    ) -> None:
        """
        Mark a file as failed.  If retry=True and attempt_count < max_attempts,
        schedules a retry via next_retry_at.
        """
        try:
            row_result = (
                self._db.table("ingestion_queue")
                .select("attempt_count, max_attempts")
                .eq("id", file_id)
                .execute()
            )
            row = (row_result.data or [{}])[0]
            attempt = int(row.get("attempt_count", 0)) + 1
            max_att = int(row.get("max_attempts", 3))
        except Exception:
            attempt, max_att = 1, 3

        now = datetime.now(timezone.utc)
        next_retry: Optional[str] = None
        if retry and attempt < max_att:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            next_retry = (now + timedelta(seconds=delay)).isoformat()

        try:
            self._db.table("ingestion_queue").update({
                "status": "error",
                "error_detail": error[:2000],
                "attempt_count": attempt,
                "next_retry_at": next_retry,
                "last_processed_at": now.isoformat(),
            }).eq("id", file_id).execute()
        except Exception as exc:
            log.warning("IngestionPipeline: mark_error failed for %s: %s",
                        file_id, exc)

    def mark_skipped(self, file_id: str, reason: str = "") -> None:
        """Mark a file as intentionally skipped (e.g. unsupported format)."""
        try:
            self._db.table("ingestion_queue").update({
                "status": "skipped",
                "error_detail": reason[:500] or None,
                "last_processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", file_id).execute()
        except Exception as exc:
            log.warning("IngestionPipeline: mark_skipped failed for %s: %s",
                        file_id, exc)
