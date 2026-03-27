"""
Proactive QuickBooks Online token refresh task.

Runs every 50 minutes to refresh access tokens that will expire within 15 minutes.
QBO access tokens expire after 3600 s (1 hour); this task ensures no task ever
hits the API with a stale token.

Why 50-minute interval?
  Token lifetime:  60 min
  Refresh window:  last 15 min (token has <15 min left)
  Task interval:   50 min  →  a freshly issued token is refreshed at ~55 min old,
                              well within the 60-min window.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from celery_app import app
from tasks.base import get_supabase, task_lock

log = logging.getLogger(__name__)

# Refresh tokens that expire within this many minutes
REFRESH_THRESHOLD_MINUTES = 15


@app.task(name="tasks.qbo_token_refresh.refresh_expiring_tokens", bind=True, max_retries=1)
def refresh_expiring_tokens(self):
    """
    Scan all connected companies and refresh any QBO access tokens that will
    expire within REFRESH_THRESHOLD_MINUTES.
    """
    with task_lock("qbo_token_refresh", ttl_seconds=600) as acquired:
        if not acquired:
            log.info("QBO token refresh already running — skipping")
            return {"skipped": True}

    db = get_supabase()

    # Only look at companies with an active QBO connection and tracked expiry
    result = (
        db.table("companies")
        .select("id, name, qbo_access_token, qbo_refresh_token, qbo_token_expires_at")
        .eq("qb_connection_status", "connected")
        .not_.is_("qbo_token_expires_at", "null")
        .execute()
    )
    companies = result.data or []

    threshold = datetime.now(timezone.utc) + timedelta(minutes=REFRESH_THRESHOLD_MINUTES)

    summary = {"checked": len(companies), "refreshed": 0, "errors": 0, "skipped": 0}

    from app.utils.encryption import decrypt, encrypt
    from app.auth.oauth import refresh_qbo_token
    from app.config import get_settings

    settings = get_settings()

    for company in companies:
        expires_at_str = company.get("qbo_token_expires_at")
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            summary["skipped"] += 1
            continue

        if expires_at > threshold:
            summary["skipped"] += 1
            continue  # Token is fine — no refresh needed

        # Token expires soon — refresh it
        try:
            raw_refresh = decrypt(company["qbo_refresh_token"], settings.secret_key)
            new_tokens = asyncio.run(refresh_qbo_token(raw_refresh))

            new_expires_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=new_tokens.get("expires_in", 3600))
            ).isoformat()

            db.table("companies").update({
                "qbo_access_token":     encrypt(new_tokens["access_token"], settings.secret_key),
                "qbo_refresh_token":    encrypt(new_tokens["refresh_token"], settings.secret_key),
                "qbo_token_expires_at": new_expires_at,
            }).eq("id", company["id"]).execute()

            summary["refreshed"] += 1
            log.info(
                "QBO token refreshed for company %s (was expiring at %s)",
                company["id"],
                expires_at_str,
            )

        except Exception as exc:
            summary["errors"] += 1
            log.error("QBO token refresh failed for company %s: %s", company["id"], exc)

    log.info(
        "QBO token refresh complete: %d checked, %d refreshed, %d errors, %d skipped",
        summary["checked"],
        summary["refreshed"],
        summary["errors"],
        summary["skipped"],
    )
    return summary
