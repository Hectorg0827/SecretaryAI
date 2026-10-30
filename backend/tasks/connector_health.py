"""
Connector Health Task — periodic stale-connector detection across all tenants.

Runs every 5 minutes. For each connector that hasn't heartbeated within
STALE_THRESHOLD_SECONDS (3 minutes from connector_protocol.py), sets
status = 'stale' and sends a push notification to the company's owners and
managers.

This is a belt-and-suspenders complement to the per-request stale check in
GET /api/connectors/status. The periodic task catches connectors that have gone
stale between web UI page loads.

Log lines are constant events: connector IDs and types are supplied by the
connector itself and must not be written into logs or push messages.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery_app import app
from tasks.base import get_supabase, task_lock, locked_task  # noqa: F401 (task_lock patched in tests)

log = logging.getLogger(__name__)

# Mirrors STALE_THRESHOLD_SECONDS from connector_protocol.py (180 s).
# Defined here to avoid importing the protocol module in a sync Celery context.
_STALE_SECONDS = 180
# After this long with no heartbeat, escalate from 'stale' to 'error'.
_ERROR_SECONDS = 3600  # 1 hour

# Plain-language names for the known connector types (ConnectorType in
# app/domain/connector_protocol.py). Push text only ever uses these constants,
# never the stored value, so a connector cannot inject text into a message.
_CONNECTOR_NAMES = {
    "qb_desktop": "QuickBooks Desktop",
    "qb_online": "QuickBooks Online",
    "google": "Google",
    "shopify": "Shopify",
    "file_watcher": "File folder sync",
}


class ConnectorHealthCheckFailed(RuntimeError):
    """A health query could not be read; zero stale connectors is not known."""


@app.task(name="tasks.connector_health.check_all", bind=True, max_retries=1)
@locked_task("connector_health", ttl_seconds=300)
def check_all(self):
    """
    Scan all connector_registrations and update stale / error status.
    Sends a push notification to the company's owners and managers when a
    connector first transitions to 'stale'.

    A failed status query fails the run (after both checks were attempted)
    instead of reporting zero stale connectors.
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)

    stale_cutoff = (now - timedelta(seconds=_STALE_SECONDS)).isoformat()
    error_cutoff = (now - timedelta(seconds=_ERROR_SECONDS)).isoformat()

    failed_checks: list[str] = []
    try:
        newly_stale = _mark_stale(db, stale_cutoff, now)
    except ConnectorHealthCheckFailed:
        newly_stale = None
        failed_checks.append("stale")
    try:
        newly_errored = _mark_errored(db, error_cutoff, now)
    except ConnectorHealthCheckFailed:
        newly_errored = None
        failed_checks.append("error_escalation")

    if failed_checks:
        log.error("Connector health check failed: connector status unavailable")
        raise ConnectorHealthCheckFailed("Connector status query failed")

    log.info("Connector health check complete")
    return {
        "newly_stale": newly_stale,
        "newly_errored": newly_errored,
        "checked_at": now.isoformat(),
    }


def _changed(result) -> bool:
    """PostgREST returns the updated rows; none means another writer got there first."""
    rows = getattr(result, "data", None)
    return bool(rows)


def _mark_stale(db, stale_cutoff: str, now: datetime) -> int:
    """
    Mark 'connected' connectors whose last_heartbeat is older than stale_cutoff
    as 'stale', then notify each affected company once.
    Returns count of connectors newly marked stale.
    Raises ConnectorHealthCheckFailed if the connectors cannot be read.
    """
    try:
        result = (
            db.table("connector_registrations")
            .select("id, company_id, connector_type, connector_id")
            .eq("status", "connected")
            .lt("last_heartbeat", stale_cutoff)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        log.error("Connector health: stale connector query failed")
        raise ConnectorHealthCheckFailed("Stale connector query failed") from exc

    marked = 0
    stale_by_company: dict[str, list[str]] = {}
    for reg in rows:
        try:
            # Only flip a connector that is still 'connected': a heartbeat that
            # arrived since the read must not be reported as a lost connection.
            updated = (
                db.table("connector_registrations").update({
                    "status": "stale",
                    "updated_at": now.isoformat(),
                })
                .eq("id", reg["id"])
                .eq("company_id", reg["company_id"])
                .eq("status", "connected")
                .execute()
            )
        except Exception:
            log.error("Connector health: failed to mark a connector stale")
            continue
        if not _changed(updated):
            continue
        marked += 1
        log.warning("Connector health: connector marked stale")
        stale_by_company.setdefault(reg["company_id"], []).append(reg.get("connector_type"))

    for company_id, connector_types in stale_by_company.items():
        _notify_stale(db, company_id, connector_types)

    return marked


def _mark_errored(db, error_cutoff: str, now: datetime) -> int:
    """
    Escalate 'stale' connectors with last_heartbeat older than error_cutoff to 'error'.
    Raises ConnectorHealthCheckFailed if the connectors cannot be read.
    """
    try:
        result = (
            db.table("connector_registrations")
            .select("id, company_id, connector_type, connector_id")
            .eq("status", "stale")
            .lt("last_heartbeat", error_cutoff)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        log.error("Connector health: error escalation query failed")
        raise ConnectorHealthCheckFailed("Error escalation query failed") from exc

    escalated = 0
    for reg in rows:
        try:
            updated = (
                db.table("connector_registrations").update({
                    "status": "error",
                    "updated_at": now.isoformat(),
                })
                .eq("id", reg["id"])
                .eq("company_id", reg["company_id"])
                .eq("status", "stale")
                .execute()
            )
        except Exception:
            log.error("Connector health: failed to escalate a connector to error")
            continue
        if not _changed(updated):
            continue
        escalated += 1
        log.error("Connector health: connector escalated to error")

    return escalated


def stale_push_message(connector_types: list) -> tuple[str, str]:
    """Constant-safe title and body; only our own names and a count are used."""
    title = "Connection problem"
    if len(connector_types) == 1:
        name = _CONNECTOR_NAMES.get(connector_types[0])
        if name:
            return title, (
                f"{name} stopped responding. Check that the computer running it "
                "is on and online, then open SecretaryAI to see your connections."
            )
        return title, (
            "A business connection stopped responding. "
            "Open SecretaryAI to see your connections."
        )
    return title, (
        f"{len(connector_types)} business connections stopped responding. "
        "Open SecretaryAI to see your connections."
    )


def _notify_stale(db, company_id: str, connector_types: list) -> bool:
    """
    Push to the company's owners and managers that connections went stale.
    Non-fatal: a push failure never undoes or fails the status change.
    Returns True when a push was handed to the push service.
    """
    from app.api.notifications import send_push_to_company_leaders

    title, body = stale_push_message(connector_types)
    try:
        return asyncio.run(
            send_push_to_company_leaders(
                db=db,
                company_id=company_id,
                title=title,
                body=body,
                data={"type": "connector_stale", "count": len(connector_types)},
            )
        )
    except Exception:
        log.warning("Connector health: connection problem push failed")
        return False
