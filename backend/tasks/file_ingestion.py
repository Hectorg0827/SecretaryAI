"""File ingestion task — scans watched folders for all companies every 30 minutes."""
import asyncio
import hashlib
import logging

from celery_app import app
from tasks.base import get_supabase, get_active_companies, task_lock

log = logging.getLogger(__name__)


def _content_hash(result: dict) -> str:
    """Stable SHA-256 of filename + row_count + first-row fingerprint."""
    fingerprint = f"{result['filename']}:{result['row_count']}"
    if result.get("rows"):
        fingerprint += f":{str(result['rows'][0])}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()


async def _store_ingested_file(db, company_id: str, result: dict) -> None:
    """
    Persist ingested file record to Supabase.
    Skips insertion if a record with the same file_hash already exists
    (deduplication — prevents the same file being stored twice on re-runs).
    """
    try:
        file_hash = _content_hash(result)

        # Check for existing record with same hash
        existing = (
            db.table("ingested_files")
            .select("id")
            .eq("company_id", company_id)
            .eq("file_hash", file_hash)
            .limit(1)
            .execute()
        )
        if existing.data:
            log.debug("Skipping duplicate file: %s (hash=%s)", result["filename"], file_hash[:8])
            return

        db.table("ingested_files").insert(
            {
                "company_id":  company_id,
                "filename":    result["filename"],
                "report_type": result["report_type"],
                "row_count":   result["row_count"],
                "data":        result["rows"][:500] if result.get("rows") else [],
                "file_hash":   file_hash,
            }
        ).execute()
    except Exception as exc:
        log.warning("Could not persist ingested file record: %s", exc)


@app.task(name="tasks.file_ingestion.ingest_watched_files_all", bind=True, max_retries=1)
def ingest_watched_files_all(self):
    with task_lock("file_ingestion", ttl_seconds=1800) as acquired:
        if not acquired:
            log.info("File ingestion already running — skipping")
            return {"skipped": True}
    db = get_supabase()
    companies = get_active_companies(db)
    log.info("File ingestion: %d companies", len(companies))

    results = []
    for company in companies:
        company_id = company["id"]
        watched_folders = company.get("watched_folders") or []
        if not watched_folders:
            continue
        try:
            from app.file_ingestion.watcher import FileWatcher

            async def on_file(cid: str, result: dict) -> None:
                await _store_ingested_file(db, cid, result)

            watcher = FileWatcher(
                watch_dirs=watched_folders,
                company_id=company_id,
                db=db,
                on_file=on_file,
            )
            asyncio.run(watcher.run_once())
            results.append({"company_id": company_id, "ok": True})
        except Exception as exc:
            log.error("File ingestion failed for %s: %s", company_id, exc)
            results.append({"company_id": company_id, "ok": False, "error": str(exc)})

    return results
