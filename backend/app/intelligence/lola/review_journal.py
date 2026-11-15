"""Durable, human-reviewed Lola event journal; NEVER an approval or execution queue.

Only server-derived logistics monitor findings may be journalled. An explicit
authenticated leader initiates a snapshot capture; no background dispatch,
messages, financial writes or unsolicited notification occurs.

An alert's event key includes its source-ledger revision. Repeated captures
are idempotent while a changed source version creates a fresh pending review.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import UUID

from app.intelligence.wholesale_distribution.logistics.monitoring import LogisticsMonitor
from app.intelligence.wholesale_distribution.logistics.visibility import LogisticsViewer

TABLE = "lola_review_events"
MAX_CAPTURE_ALERTS = 100
MAX_LIST = 50
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ALERT_CODE = re.compile(r"^[A-Z0-9_]{2,80}$")
_SOURCE_KIND = "logistics_alert"
_SEVERITIES = frozenset({"critical", "alert", "warning", "info"})
_FIELDS = (
    "id,company_id,event_key,source_kind,source_alert_id,source_revision,"
    "shipment_ref,alert_code,severity,evidence_quality,review_state,"
    "first_seen_at,acknowledged_at,acknowledged_by"
)


class ReviewJournalUnavailable(RuntimeError):
    """Missing migration, source outage, or unverifiable persistence result."""


class ReviewJournalDenied(PermissionError):
    """Token role is stale, employee disabled, or user has no review permission."""


class ReviewRecordNotFound(LookupError):
    """Requested record is absent from the authenticated company."""


class ReviewConflict(ValueError):
    """A source revision or review state changed concurrently."""


def _company_user(user: dict) -> tuple[str, str, str]:
    if not isinstance(user, dict):
        raise ReviewJournalDenied("Missing employee identity")
    company = user.get("company_id")
    actor = user.get("sub")
    role = user.get("role")
    if (not isinstance(company, str) or not company or not isinstance(actor, str)
            or not actor or role not in ("owner", "manager")):
        raise ReviewJournalDenied("Executive review access denied")
    return company, actor, role


def require_active_leader(user: dict, db) -> tuple[str, str]:
    """Revalidate current active DB membership; do not trust stale JWT roles."""
    company, actor, role = _company_user(user)
    try:
        rows = (db.table("users").select("id,company_id,role,is_active")
                .eq("id", actor).eq("company_id", company).limit(2).execute().data)
    except Exception:
        raise ReviewJournalUnavailable("Employee authorization source unavailable") from None
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or str(rows[0].get("id")) != actor
            or str(rows[0].get("company_id")) != company
            or rows[0].get("role") != role or rows[0].get("is_active") is not True):
        raise ReviewJournalDenied("Current employee membership not authorized")
    return company, actor


def _verified_rows(response, *, company: str) -> list[dict]:
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or str(row.get("company_id")) != company for row in rows
    ):
        raise ReviewJournalUnavailable("Journal storage response invalid")
    return rows


def _storage(query, company: str) -> list[dict]:
    try:
        return _verified_rows(query.execute(), company=company)
    except ReviewJournalUnavailable:
        raise
    except Exception:
        raise ReviewJournalUnavailable("Journal storage unavailable") from None


def _record_projection(row: dict) -> dict:
    return {key: row.get(key) for key in (
        "id", "source_alert_id", "shipment_ref", "alert_code", "severity",
        "evidence_quality", "source_revision", "review_state",
        "first_seen_at", "acknowledged_at", "acknowledged_by",
    )}


def list_reviews(*, user: dict, db, limit: int = 20, offset: int = 0,
                 pending_only: bool = False) -> dict:
    company, _ = require_active_leader(user, db)
    if type(limit) is not int or not 1 <= limit <= MAX_LIST or type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError("Invalid list bounds")
    query = (db.table(TABLE).select(_FIELDS).eq("company_id", company)
             .order("first_seen_at", desc=True).order("id", desc=True))
    if pending_only:
        query = query.eq("review_state", "pending")
    rows = _storage(query.range(offset, offset + limit), company)
    if len(rows) > limit + 1:
        raise ReviewJournalUnavailable("Unexpected journal page size")
    seen: set[str] = set()
    for row in rows:
        rid = str(row.get("id") or "")
        if not rid or rid in seen or row.get("review_state") not in ("pending", "acknowledged"):
            raise ReviewJournalUnavailable("Malformed or duplicate review")
        seen.add(rid)
        if not _DIGEST.fullmatch(str(row.get("source_revision") or "")):
            raise ReviewJournalUnavailable("Review source revision invalid")
    return {
        "scope": "recorded_logistics_reviews_only",
        "state": "recorded_reviews" if rows else "no_recorded_review_events",
        "items": [_record_projection(r) for r in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
        "company_visibility": "active_owner_or_manager",
        "recording_is_manual": True,
        "review_does_not_approve_external_actions": True,
        "notifications_sent": False,
        "external_writes_executed": False,
        "coverage_note": (
            "Persisted review records are not a complete business-alert inventory. "
            "This endpoint does not inspect live email, WhatsApp, QuickBooks or carriers."
        ),
    }


def build_capture(*, user: dict, ledger, as_of: date) -> dict:
    """Compute tenant-scoped immutable rows before touching the database."""
    company, _, _ = _company_user(user)
    if not isinstance(as_of, date) or isinstance(as_of, datetime) or as_of != date.today():
        raise ValueError("Capture only the current calendar day")
    if str(getattr(ledger, "company_id", "")) != company:
        raise ReviewJournalUnavailable("Logistics ledger company mismatch")
    shipments = ledger.shipments()
    if any(getattr(shipment, "company_id", None) != company for shipment in shipments):
        raise ReviewJournalUnavailable("Logistics shipment tenant mismatch")
    shipment_ids = {str(s.shipment_id) for s in shipments}
    events = ledger.events()
    keys: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if getattr(event, "company_id", None) != company:
            raise ReviewJournalUnavailable("Logistics event tenant mismatch")
        shipment_id = getattr(event, "shipment_id", None)
        if not isinstance(shipment_id, str) or shipment_id not in shipment_ids:
            raise ReviewJournalUnavailable("Unscoped logistics source event")
        source_key = getattr(event, "idempotency_key", None)
        if not isinstance(source_key, str) or not source_key.strip() or len(source_key) > 160:
            raise ReviewJournalUnavailable("Missing event provenance key")
        keys[shipment_id].add(source_key)
    viewer = LogisticsViewer.from_user(user)
    viewer.require_access()
    alerts = LogisticsMonitor(ledger).scan(as_of)
    permitted = viewer.filter_alerts(alerts, {s.shipment_id: s for s in shipments})
    if len(permitted) > MAX_CAPTURE_ALERTS:
        raise ReviewJournalUnavailable("Too many alerts for atomic capture")
    batch = {}
    unscoped = 0
    for alert in permitted:
        shipment = alert.get("shipment_id")
        if not shipment:
            # Cross-shipment supplier analytics must be modeled separately.
            unscoped += 1
            continue
        if (not isinstance(shipment, str) or shipment not in shipment_ids
                or not 1 <= len(shipment) <= 160 or not keys.get(shipment)):
            raise ReviewJournalUnavailable("Alert cannot be tied to a source shipment")
        alert_id, code, severity = alert.get("alert_id"), alert.get("code"), alert.get("severity")
        if (not isinstance(alert_id, str) or not 1 <= len(alert_id) <= 160
                or not isinstance(code, str) or not _ALERT_CODE.fullmatch(code)
                or severity not in _SEVERITIES):
            raise ReviewJournalUnavailable("Malformed monitor alert")
        revision = hashlib.sha256(
            (company + "\n" + shipment + "\n"
             + "\n".join(sorted(keys[shipment]))).encode("utf-8")
        ).hexdigest()
        event_key = hashlib.sha256(
            (company + "\n" + _SOURCE_KIND + "\n" + shipment + "\n"
             + alert_id + "\n" + revision).encode("utf-8")
        ).hexdigest()
        quality = ("unverified_report_requires_confirmation"
                   if code == "UNVERIFIED_REPORT" else "monitor_derived")
        record = {
            "company_id": company, "event_key": event_key,
            "source_kind": _SOURCE_KIND, "source_alert_id": alert_id,
            "source_revision": revision, "shipment_ref": shipment,
            "alert_code": code, "severity": severity,
            "evidence_quality": quality, "review_state": "pending",
        }
        prior = batch.setdefault(event_key, record)
        if prior != record:
            raise ReviewJournalUnavailable("Ambiguous alert provenance")
    return {
        "records": list(batch.values()),
        "observed_alerts": len(permitted),
        "skipped_unscoped_alerts": unscoped,
    }


def capture_reviews(*, user: dict, db, ledger, as_of: date | None = None) -> dict:
    """One atomic idempotent DB statement; no notification or execution."""
    company, _ = require_active_leader(user, db)
    packet = build_capture(user=user, ledger=ledger, as_of=as_of or date.today())
    if packet["records"]:
        try:
            # PostgREST upsert(ignore_duplicates) uses ON CONFLICT DO NOTHING.
            # This MUST NOT reset an existing human acknowledgement to pending.
            response = db.table(TABLE).upsert(
                packet["records"], on_conflict="company_id,event_key",
                ignore_duplicates=True,
            ).execute()
            # Supabase can return [] for duplicates or all rows if inserted.
            data = getattr(response, "data", None)
            if data is not None and not isinstance(data, list):
                raise ReviewJournalUnavailable("Journal persistence response invalid")
            if isinstance(data, list) and any(
                str(r.get("company_id")) != company for r in data
            ):
                raise ReviewJournalUnavailable("Journal persistence tenant mismatch")
        except ReviewJournalUnavailable:
            raise
        except Exception:
            raise ReviewJournalUnavailable("Journal persistence unavailable") from None
    return {
        "state": "capture_completed" if packet["records"] else "no_scoped_alerts_detected",
        "candidate_records": len(packet["records"]),
        "observed_alerts": packet["observed_alerts"],
        "skipped_unscoped_alerts": packet["skipped_unscoped_alerts"],
        "deduplication": "company_event_key_on_conflict_do_nothing",
        "recorded_change_count": None,
        "approval_created": False, "notifications_sent": False,
        "external_writes_executed": False,
        "coverage_note": (
            "Manual capture of current logistics monitor evidence only; "
            "existing acknowledgements are never automatically reopened. "
            "Unscoped supplier analytics are not persisted."
        ),
    }


def acknowledge_review(*, user: dict, db, review_id: UUID, expected_revision: str) -> dict:
    """CAS a pending review; human acknowledges observation, not action."""
    company, actor = require_active_leader(user, db)
    if not isinstance(expected_revision, str) or not _DIGEST.fullmatch(expected_revision):
        raise ValueError("Invalid expected source revision")
    rid = str(review_id)
    query = (db.table(TABLE).select(_FIELDS).eq("company_id", company)
             .eq("id", rid).limit(2))
    rows = _storage(query, company)
    if not rows:
        raise ReviewRecordNotFound("Review record not found")
    if len(rows) != 1 or str(rows[0].get("id")) != rid:
        raise ReviewJournalUnavailable("Ambiguous review identity")
    current = rows[0]
    if current.get("source_revision") != expected_revision:
        raise ReviewConflict("Review source changed; refresh before acknowledging")
    if current.get("review_state") == "acknowledged":
        return {"review_id": rid, "review_state": "acknowledged",
                "already_acknowledged": True, "external_writes_executed": False}
    if current.get("review_state") != "pending":
        raise ReviewJournalUnavailable("Review state invalid")
    try:
        updated = (db.table(TABLE)
                   .update({"review_state": "acknowledged", "acknowledged_by": actor,
                            "acknowledged_at": datetime.now(timezone.utc).isoformat()})
                   .eq("company_id", company).eq("id", rid)
                   .eq("source_revision", expected_revision)
                   .eq("review_state", "pending").execute())
        changed = _verified_rows(updated, company=company)
    except ReviewJournalUnavailable:
        raise
    except Exception:
        raise ReviewJournalUnavailable("Review acknowledgement unavailable") from None
    if len(changed) != 1 or str(changed[0].get("id")) != rid:
        raise ReviewConflict("Review already changed; reload journal")
    if (changed[0].get("review_state") != "acknowledged"
            or changed[0].get("source_revision") != expected_revision
            or str(changed[0].get("acknowledged_by")) != actor):
        raise ReviewJournalUnavailable("Acknowledgement write not verifiable")
    return {
        "review_id": rid, "review_state": "acknowledged",
        "already_acknowledged": False, "action_approved": False,
        "external_writes_executed": False,
    }
