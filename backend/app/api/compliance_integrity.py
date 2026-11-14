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


def save_record(db, table: str, record: dict) -> None:
    """Confirm id/company match in the storage response before acknowledgement."""
    try:
        # The service-role client bypasses RLS. Check the global primary-key
        # owner before an upsert so supplied IDs cannot overwrite another tenant.
        existing = observed_rows(
            db.table(table).select("id,company_id").eq("id", record["id"])
            .limit(1).execute()
        )
        if any(str(row.get("company_id")) != str(record["company_id"]) for row in existing):
            raise HTTPException(status_code=403, detail="Compliance record belongs to another company")
        result = db.table(table).upsert(record).execute()
        rows = observed_rows(result)
        if (len(rows) != 1
                or str(rows[0].get("id")) != str(record["id"])
                or str(rows[0].get("company_id")) != str(record["company_id"])):
            raise ValueError("Unconfirmed write result")
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Compliance persistence unverified table=%s error_type=%s", table, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Compliance record was not verified as saved") from exc


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
    """Require exactly one confirmed deleted row; never claim a no-op deleted it."""
    try:
        rows = observed_rows(
            db.table(table).delete().eq("id", record_id)
            .eq("company_id", company_id).execute()
        )
    except Exception as exc:
        log.error("Compliance deletion unverified table=%s error_type=%s", table, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Compliance deletion could not be verified") from exc
    if not rows:
        raise HTTPException(status_code=404, detail="Compliance record not found")
    if len(rows) != 1 or str(rows[0].get("id")) != str(record_id):
        raise HTTPException(status_code=503, detail="Compliance deletion result incomplete")
