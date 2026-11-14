"""Opt-in private Lola reminders. NO company broadcast, NO outbound sales message.

A generic push opens the user's authenticated private Lola follow-up center.
Message subjects, contact names and snippets never leave the private API.
"""
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from celery_app import app
from tasks.base import get_supabase, locked_task
from app.api.private_continuity import private_followups
from app.api.notifications import send_push_notifications

log = logging.getLogger(__name__)


@app.task(name="tasks.lola_private_reminders.scan_all", bind=True, max_retries=1)
@locked_task(lambda self: "lola_private_reminders", ttl_seconds=3300)
def scan_all(self):
    db = get_supabase()
    try:
        prefs = (db.table("lola_private_preferences")
            .select("company_id,user_id,timezone,reminder_hour")
            .eq("proactive_enabled", True).eq("push_enabled", True)
            .limit(5000).execute()).data or []
    except Exception:
        log.warning("Private reminder preferences unavailable; no notifications sent")
        return {"sent": 0, "scanned": 0}

    now = datetime.now(timezone.utc)
    sent = 0
    for pref in prefs:
        company_id, user_id = pref.get("company_id"), pref.get("user_id")
        if not company_id or not user_id:
            continue
        try:
            local = now.astimezone(ZoneInfo(pref["timezone"]))
            if local.hour != pref["reminder_hour"]:
                continue
            # The employee must still be active and in the same tenant.
            active = (db.table("users").select("id")
                .eq("id", user_id).eq("company_id", company_id)
                .eq("is_active", True).limit(1).execute()).data or []
            if not active:
                continue
            # Never consume a one-time reminder receipt before the employee
            # registers a device. Otherwise a missing phone makes a future
            # legitimately due reminder permanently undeliverable.
            device = (db.table("device_push_tokens").select("token")
                .eq("company_id", company_id).eq("user_id", user_id)
                .limit(1).execute()).data or []
            if not device:
                continue
            due = private_followups(
                user={"company_id": company_id, "sub": user_id}, db=db
            )["followups"]
            for item in due[:10]:
                event_id = item["event_id"]
                prior = (db.table("lola_private_reminder_receipts").select("event_id")
                    .eq("company_id", company_id).eq("user_id", user_id)
                    .eq("event_id", event_id).limit(1).execute()).data or []
                if prior:
                    continue
                # Reserve the unique per-employee receipt before sending,
                # preventing parallel workers from delivering duplicate pushes.
                receipt = (db.table("lola_private_reminder_receipts").insert({
                    "company_id": company_id, "user_id": user_id,
                    "event_id": event_id,
                }).execute()).data or []
                if not receipt:
                    continue
                accepted = asyncio.run(send_push_notifications(
                    db=db, company_id=company_id,
                    title="Lola · Private follow-up",
                    body="You have a pending follow-up to review.",
                    data={"type": "lola_private_followup"},
                    user_ids=[user_id],  # NEVER omit, widen or broadcast.
                ))
                if accepted:
                    db.table("lola_private_reminder_receipts").update({
                        "push_accepted": True
                    }).eq("company_id", company_id).eq("user_id", user_id).eq(
                        "event_id", event_id
                    ).execute()
                    sent += 1
        except (ValueError, ZoneInfoNotFoundError):
            log.warning("Invalid private reminder timezone/configuration")
        except Exception:
            log.warning("Private reminder scan failed for one employee")
    return {"sent": sent, "scanned": len(prefs)}
