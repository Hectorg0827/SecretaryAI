"""
Shared helpers for Celery tasks — DB connection, adapter factory, task locks.
"""
import contextlib
import logging
from datetime import date
from supabase import create_client, Client
from app.config import get_settings
from app.connectors.unified_adapter import UnifiedDataAdapter

log = logging.getLogger(__name__)
settings = get_settings()

# ── Supabase singleton for tasks (pooled) ─────────────────────────────────────
_supabase_client: Client | None = None


def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
    return _supabase_client


# ── Redis-backed task idempotency lock ────────────────────────────────────────

@contextlib.contextmanager
def task_lock(task_name: str, ttl_seconds: int = 3600):
    """
    Acquire a Redis distributed lock for a task (keyed by task_name + today's date).
    Yields True if the lock was acquired, False if another worker already holds it.

    Usage:
        with task_lock("overnight_scan") as acquired:
            if not acquired:
                log.info("Already running — skipping")
                return
            ... do work ...

    The lock is released automatically on exit (or after ttl_seconds if the
    process dies before the context manager exits).
    """
    import redis as redis_lib

    lock_key = f"task_lock:{task_name}:{date.today().isoformat()}"
    r = redis_lib.from_url(settings.redis_url, decode_responses=True)

    acquired = r.set(lock_key, "1", nx=True, ex=ttl_seconds)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            try:
                r.delete(lock_key)
            except Exception:
                pass  # Lock TTL will expire it anyway


def get_active_companies(db: Client) -> list[dict]:
    """Fetch all companies with an active QB connection."""
    result = (
        db.table("companies")
        .select("*")
        .neq("qb_connection_status", "disconnected")
        .execute()
    )
    return result.data or []


def _refresh_qbo_if_needed(company: dict, cfg: dict, db) -> None:
    """
    Proactively refresh the QBO access token if it expires within 10 minutes.
    Updates cfg in-place and persists new tokens + expiry to the DB.
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from app.utils.encryption import encrypt

    expires_at_str = company.get("qbo_token_expires_at")
    if not expires_at_str:
        return  # No expiry tracked — skip (will refresh on next QBO API 401)

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return

    if datetime.now(timezone.utc) < expires_at - timedelta(minutes=10):
        return  # Still valid for >10 minutes — no refresh needed

    refresh_token = cfg.get("qbo_refresh_token")
    if not refresh_token:
        return

    try:
        from app.auth.oauth import refresh_qbo_token
        new_tokens = asyncio.run(refresh_qbo_token(refresh_token))

        new_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=new_tokens.get("expires_in", 3600))
        ).isoformat()

        cfg["qbo_access_token"] = new_tokens["access_token"]
        cfg["qbo_refresh_token"] = new_tokens["refresh_token"]

        db.table("companies").update({
            "qbo_access_token":    encrypt(new_tokens["access_token"], settings.secret_key),
            "qbo_refresh_token":   encrypt(new_tokens["refresh_token"], settings.secret_key),
            "qbo_token_expires_at": new_expires_at,
        }).eq("id", company["id"]).execute()

        log.info("QBO access token refreshed for company %s", company["id"])
    except Exception as exc:
        log.warning("QBO token refresh failed for company %s: %s", company["id"], exc)


def build_adapter(company: dict) -> UnifiedDataAdapter:
    """Build a UnifiedDataAdapter from a company row."""
    from app.utils.encryption import decrypt
    cfg: dict = dict(company)

    # Decrypt sensitive fields
    if cfg.get("qbo_access_token"):
        cfg["qbo_access_token"] = decrypt(cfg["qbo_access_token"], settings.secret_key)
    if cfg.get("qbo_refresh_token"):
        cfg["qbo_refresh_token"] = decrypt(cfg["qbo_refresh_token"], settings.secret_key)
    if cfg.get("google_access_token"):
        cfg["google_access_token"] = decrypt(cfg["google_access_token"], settings.secret_key)
    if cfg.get("google_refresh_token"):
        cfg["google_refresh_token"] = decrypt(cfg["google_refresh_token"], settings.secret_key)

    # Proactively refresh QBO access token if close to expiry
    if cfg.get("qbo_access_token") and cfg.get("qbo_refresh_token"):
        _refresh_qbo_if_needed(company, cfg, get_supabase())

    # Build gmail_credentials dict that GmailConnector expects (refresh token if expired)
    if cfg.get("google_access_token") and cfg.get("google_refresh_token"):
        google_creds = {
            "token":         cfg["google_access_token"],
            "refresh_token": cfg["google_refresh_token"],
            "client_id":     settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
        try:
            from app.auth.oauth import refresh_google_token_if_needed
            from app.utils.encryption import encrypt
            updated_creds, was_refreshed = refresh_google_token_if_needed(google_creds)
            if was_refreshed:
                db = get_supabase()
                new_encrypted = encrypt(updated_creds["token"], settings.secret_key)
                db.table("companies").update({
                    "google_access_token": new_encrypted,
                }).eq("id", company["id"]).execute()
                google_creds["token"] = updated_creds["token"]
        except Exception as exc:
            log.warning("Google token refresh failed in task: %s", exc)
        cfg["gmail_credentials"] = google_creds

    # Map column names to adapter config keys
    cfg.setdefault("qbo_client_id", settings.intuit_client_id)
    cfg.setdefault("qbo_client_secret", settings.intuit_client_secret)
    cfg.setdefault("conductor_api_key", settings.conductor_api_key)

    return UnifiedDataAdapter(cfg)
