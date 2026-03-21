"""
Shared helpers for Celery tasks — DB connection, adapter factory.
"""
import logging
from supabase import create_client, Client
from app.config import get_settings
from app.connectors.unified_adapter import UnifiedDataAdapter

log = logging.getLogger(__name__)
settings = get_settings()


def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


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

    # Build gmail_credentials dict that GmailConnector expects
    if cfg.get("google_access_token") and cfg.get("google_refresh_token"):
        cfg["gmail_credentials"] = {
            "token":         cfg["google_access_token"],
            "refresh_token": cfg["google_refresh_token"],
            "client_id":     settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "token_uri":     "https://oauth2.googleapis.com/token",
        }

    # Map column names to adapter config keys
    cfg.setdefault("qbo_client_id", settings.intuit_client_id)
    cfg.setdefault("qbo_client_secret", settings.intuit_client_secret)
    cfg.setdefault("conductor_api_key", settings.conductor_api_key)

    return UnifiedDataAdapter(cfg)
