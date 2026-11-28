"""
Compliance alert check — runs daily at 8 AM UTC.
Generates expiration alerts for licenses, permits, brand registrations, and COLA
records, then stores the digest in report_snapshots for all employees to read
(no automatic sending — a human reviews and acts from the Compliance UI).
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


async def check_compliance_alerts(company_id: str, db) -> dict:
    """
    Run the compliance alert engine for one company and upsert the result
    to report_snapshots.  Returns a summary dict for Celery logging.

    Parameters
    ----------
    company_id : str
        UUID of the company row in Supabase.
    db : supabase.Client
        Service-role Supabase client (bypasses RLS).

    Returns
    -------
    dict with keys: critical, warning, info, deadlines, stored
    """
    from app.api.compliance import _get_compliance_module

    result: dict = {
        "critical": 0,
        "warning": 0,
        "info": 0,
        "deadlines": 0,
        "stored": False,
        "error": None,
    }

    try:
        # Use the same tenant-scoped, fail-closed source as the compliance API.
        # The former scheduler-only loader silently ignored read and schema errors.
        module = _get_compliance_module({"company_id": company_id}, db)

        # Expiration reminders can be drawn from persisted document dates.
        alerts = module.get_alerts(lookahead_days=90)

        # Regulatory filing and price-posting dates in the legacy state_matrix
        # are NOT human-verified. Do not turn those estimates into reminders.
        # The verified-rulebook workstream will supply these deadlines later.
        deadlines = []

        for alert in alerts:
            priority = getattr(alert, "priority", None)
            priority = getattr(priority, "value", priority)
            if priority == "critical":
                result["critical"] += 1
            elif priority == "warning":
                result["warning"] += 1
            else:
                result["info"] += 1
        result["deadlines"] = len(deadlines)

        # Serialise for storage
        def _serialise_alert(a) -> dict:
            d = {}
            for field in (
                "id", "alert_type", "priority", "state_code", "item_name",
                "days_until", "message", "action_required", "estimated_fee",
            ):
                val = getattr(a, field, None)
                d[field] = getattr(val, "value", val)
            expiry = getattr(a, "expiry_date", None)
            d["expiry_date"] = expiry.isoformat() if expiry else None
            d["renewal_url"] = getattr(a, "renewal_url", None)
            return d

        def _serialise_deadline(dl) -> dict:
            d = {}
            for field in ("deadline_type", "state_code", "frequency", "action", "notes"):
                d[field] = getattr(dl, field, None)
            due = getattr(dl, "due_date", None)
            d["due_date"] = due.isoformat() if due else None
            return d

        payload = {
            "company_id": company_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "critical_count": result["critical"],
            "warning_count": result["warning"],
            "info_count": result["info"],
            "deadline_count": result["deadlines"],
            "alerts": [_serialise_alert(a) for a in alerts],
            "deadlines": [_serialise_deadline(d) for d in deadlines],
            "regulatory_deadlines_status": "verification_required",
        }

        # Upsert to report_snapshots — one row per (company, report_type)
        written = db.table("report_snapshots").upsert(
            {
                "company_id": company_id,
                "report_type": "compliance_alerts",
                "payload": payload,
                "generated_at": payload["generated_at"],
                "generated_by": "scheduler",
            },
            on_conflict="company_id,report_type",
        ).execute()
        from app.api.compliance_integrity import observed_rows
        saved_rows = observed_rows(written)
        if (len(saved_rows) != 1
                or str(saved_rows[0].get("company_id")) != str(company_id)
                or saved_rows[0].get("report_type") != "compliance_alerts"):
            raise ValueError("Compliance snapshot write was not confirmed")

        result["stored"] = True
        log.info(
            "Compliance alert check for %s: %d critical, %d warning, %d info, %d deadlines",
            company_id,
            result["critical"],
            result["warning"],
            result["info"],
            result["deadlines"],
        )

    except Exception as exc:
        result["error"] = str(exc)
        log.error("Compliance alert check failed for %s: %s", company_id, exc)

    return result

