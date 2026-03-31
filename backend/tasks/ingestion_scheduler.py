"""
Ingestion Scheduler — Celery task that drives the hardened ingestion pipeline.

Runs every 15 minutes (beat schedule).  For each active company that has
watched_folders configured, the task:

  1. Scans each watched directory for supported files.
  2. Hashes each file and registers it in the ingestion_queue table via
     IngestionPipeline.register_file() — deduplicated by (path, hash).
  3. Pulls pending / retry-eligible rows from the queue (up to BATCH_SIZE).
  4. Calls the existing FileWatcher / parser pipeline for each file.
  5. Marks rows done or error (with back-off retry) in the queue.

This replaces the old tasks.file_ingestion task's ad-hoc approach with a
durable queue that survives worker crashes and avoids re-processing identical
files.
"""
from __future__ import annotations

import asyncio
import logging

from celery_app import app
from tasks.base import get_supabase, get_active_companies, task_lock

log = logging.getLogger(__name__)

BATCH_SIZE = 20   # max files processed per company per run


@app.task(name="tasks.ingestion_scheduler.run", bind=True, max_retries=1)
def run(self):
    """
    Scan watched directories for all active companies and process new /
    retry-eligible files through the ingestion pipeline.
    """
    with task_lock("ingestion_scheduler", ttl_seconds=900) as acquired:
        if not acquired:
            log.info("Ingestion scheduler already running — skipping")
            return {"skipped": True}

    db = get_supabase()
    companies = get_active_companies(db)
    log.info("Ingestion scheduler: %d companies", len(companies))

    summary: list[dict] = []
    for company in companies:
        company_id = company["id"]
        watched_folders = company.get("watched_folders") or []
        if not watched_folders:
            continue
        try:
            result = asyncio.run(_process_company(db, company_id, watched_folders))
            summary.append({"company_id": company_id, **result})
        except Exception as exc:
            log.error("Ingestion scheduler: company %s failed: %s", company_id, exc)
            summary.append({"company_id": company_id, "ok": False, "error": str(exc)})

    return summary


async def _process_company(
    db,
    company_id: str,
    watched_folders: list[str],
) -> dict:
    """
    Run one company's ingestion cycle.  Returns a summary dict.
    """
    from app.services.ingestion_pipeline import IngestionPipeline

    pipeline = IngestionPipeline(db, company_id)

    # ── Phase A: Scan directories and register new files ──────────────────────
    registered_new = 0
    for folder in watched_folders:
        files = IngestionPipeline.scan_directory(folder)
        for meta in files:
            file_hash = IngestionPipeline.compute_hash(meta["path"])
            if file_hash is None:
                continue
            _, is_new = pipeline.register_file(
                meta["path"],
                file_hash,
                file_size=meta["size"],
            )
            if is_new:
                registered_new += 1

    # ── Phase B: Process pending / retry-eligible rows ────────────────────────
    pending = pipeline.get_pending(limit=BATCH_SIZE)
    processed = skipped = errors = 0

    for row in pending:
        file_id = row["id"]
        file_path = row["file_path"]
        pipeline.mark_processing(file_id)

        try:
            rows_ingested = await _ingest_file(db, company_id, file_path)
            pipeline.mark_done(file_id, rows_ingested)
            processed += 1
        except _SkipFile as exc:
            pipeline.mark_skipped(file_id, str(exc))
            skipped += 1
        except Exception as exc:
            log.warning("Ingestion: error processing %s: %s", file_path, exc)
            pipeline.mark_error(file_id, str(exc))
            errors += 1

    log.info(
        "Ingestion scheduler [%s]: registered_new=%d processed=%d "
        "skipped=%d errors=%d",
        company_id, registered_new, processed, skipped, errors,
    )
    return {
        "ok": True,
        "registered_new": registered_new,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
    }


async def _ingest_file(db, company_id: str, file_path: str) -> int:
    """
    Parse a file through the existing file_ingestion pipeline.
    Returns the number of data rows ingested.
    Raises _SkipFile for unsupported / empty files.
    Raises any other exception for errors (will be retried).
    """
    from app.file_ingestion.parsers import parse_file
    from app.file_ingestion.watcher import SUPPORTED_EXTENSIONS
    from pathlib import Path

    path = Path(file_path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise _SkipFile(f"unsupported extension: {path.suffix}")

    result = parse_file(file_path)
    if not result:
        raise _SkipFile("parser returned no data")

    rows = result.get("rows") or []
    if not rows:
        raise _SkipFile("no rows parsed")

    # Store via legacy helper for backward compatibility
    from tasks.file_ingestion import _store_ingested_file
    await _store_ingested_file(db, company_id, result)
    return len(rows)


class _SkipFile(Exception):
    """Raised when a file should be skipped without counting as an error."""
