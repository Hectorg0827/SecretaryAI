"""
Connector Health Task — periodic stale-connector detection across all tenants.

Runs every 5 minutes. For each connector that hasn't heartbeated within
STALE_THRESHOLD_SECONDS (3 minutes from connector_protocol.py), sets
status = 'stale' and optionally triggers a push notification to the owner.

This is a belt-and-suspenders complement to the per-request stale check in
GET /api/connectors/status. The periodic task catches connectors that have gone
stale between web UI page loads.
"""
import logging
from datetime import datetime, timedelta, timezone

from celery_app import app
from tasks.base import get_supabase, task_lock, locked_task

log = logging.getLogger(__name__)

# Mirrors STALE_THRESHOLD_SECONDS from connector_protocol.py (180 s).
# Defined here to avoid importing the protocol module in a sync Celery context.
_STALE_SECONDS = 180
# After this long with no heartbeat, escalate from 'stale' to 'error'.
_ERROR_SECONDS = 3600  # 1 hour


@app.task(name="tasks.connector_health.check_all", bind=True, max_retries=1)
@locked_task("connector_health", ttl_seconds=300)
def check_all(self):
    """
    Scan all connector_registrations and update stale / error status.
    Sends a push notification to the company owner when a connector first
    transitions to 'stale'.
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)

    stale_cutoff = (now - timedelta(seconds=_STALE_SECONDS)).isoformat()
    error_cutoff = (now - timedelta(seconds=_ERROR_SECONDS)).isoformat()

    newly_stale = _mark_stale(db, stale_cutoff, now)
    newly_errored = _mark_errored(db, error_cutoff, now)

    summary = {
        "newly_stale": newly_stale,
        "newly_errored": newly_errored,
        "checked_at": now.isoformat(),
    }
    log.info("Connector health check: %s", summary)
    return summary


def _mark_stale(db, stale_cutoff: str, now: datetime) -> int:
    """
    Mark 'connected' connectors whose last_heartbeat is older than stale_cutoff
    as 'stale'. Returns count of connectors newly marked stale.
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
        log.error("connector_health: stale query failed: %s", exc)
        return 0

    if not rows:
        return 0

    for reg in rows:
        try:
            db.table("connector_registrations").update({
                "status": "stale",
                "updated_at": now.isoformat(),
            }).eq("id", reg["id"]).execute()

            log.warning(
                "Connector marked stale: type=%s id=%s company=%s",
                reg["connector_type"], reg["connector_id"], reg["company_id"],
            )

            # Notify company owner (non-fatal)
            _notify_stale(db, reg["company_id"], reg["connector_type"], reg["connector_id"])

        except Exception as exc:
            log.error("Failed to mark connector %s stale: %s", reg["id"], exc)

    return len(rows)


def _mark_errored(db, error_cutoff: str, now: datetime) -> int:
    """
    Escalate 'stale' connectors with last_heartbeat older than error_cutoff to 'error'.
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
        log.error("connector_health: error escalation query failed: %s", exc)
        return 0

    if not rows:
        return 0

    for reg in rows:
        try:
            db.table("connector_registrations").update({
                "status": "error",
                "updated_at": now.isoformat(),
            }).eq("id", reg["id"]).execute()

            log.error(
                "Connector escalated to error: type=%s id=%s company=%s",
                reg["connector_type"], reg["connector_id"], reg["company_id"],
            )
        except Exception as exc:
            log.error("Failed to escalate connector %s to error: %s", reg["id"], exc)

    return len(rows)


def _notify_stale(db, company_id: str, connector_type: str, connector_id: str) -> None:
    """Send a push notification to the owner when a connector goes stale."""
    try:
        result = (
            db.table("device_push_tokens")
            .select("token, platform")
            .eq("company_id", company_id)
            .execute()
        )
        tokens = result.data or []
        if not tokens:
            return

        from app.notifications.push import send_push_notifications
        import asyncio
        message = (
            f"Connector '{connector_type}' ({connector_id}) has gone offline. "
            "Please check your local machine."
        )
        asyncio.get_event_loop().run_until_complete(
            send_push_notifications(
                tokens=[t["token"] for t in tokens],
                title="Connector Offline",
                body=message,
                data={"type": "connector_stale", "company_id": company_id},
            )
        )
    except Exception as exc:
        log.debug("Stale connector push notification failed (non-fatal): %s", exc)
