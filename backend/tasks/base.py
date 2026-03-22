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
