"""
FastAPI dependencies shared across API routes.

  get_db()      → Supabase service-role client (module-level singleton — pooled)
  get_adapter() → UnifiedDataAdapter built from the current user's company row
"""
import logging
from typing import Optional

from fastapi import Depends, HTTPException

from app.auth.rbac import get_current_user
from app.config import get_settings
from app.utils.encryption import decrypt, encrypt

log = logging.getLogger(__name__)

# ── Module-level Supabase singleton (connection pooling) ──────────────────────
# Re-using one client avoids creating a new HTTP session per request.
# The supabase-py client is thread-safe for read operations.
_db_client = None


def get_db():
    """Return the module-level Supabase service-role client (pooled)."""
    global _db_client
    if _db_client is None:
        from supabase import create_client
        settings = get_settings()
        _db_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _db_client


def _load_company(company_id: str, db):
    """Fetch company row from Supabase."""
    result = db.table("companies").select("*").eq("id", company_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Company not found")
    return result.data[0]


def get_adapter(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Build a UnifiedDataAdapter for the authenticated user's company.
    Decrypts OAuth tokens before constructing the adapter.
    Raises 404 if company not found, 503 if QB not connected.
    """
    from app.connectors.unified_adapter import UnifiedDataAdapter

    settings = get_settings()
    company = _load_company(user["company_id"], db)

    cfg = dict(company)

    # Decrypt sensitive tokens
    if cfg.get("qbo_access_token"):
        cfg["qbo_access_token"] = decrypt(cfg["qbo_access_token"], settings.secret_key)
    if cfg.get("qbo_refresh_token"):
        cfg["qbo_refresh_token"] = decrypt(cfg["qbo_refresh_token"], settings.secret_key)
    if cfg.get("google_access_token"):
        cfg["google_access_token"] = decrypt(cfg["google_access_token"], settings.secret_key)
    if cfg.get("google_refresh_token"):
        cfg["google_refresh_token"] = decrypt(cfg["google_refresh_token"], settings.secret_key)

    # Build the gmail_credentials dict that GmailConnector expects.
    # Refresh the access token proactively if it is expired before building the adapter.
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
            updated_creds, was_refreshed = refresh_google_token_if_needed(google_creds)
            if was_refreshed:
                new_encrypted = encrypt(updated_creds["token"], settings.secret_key)
                db.table("companies").update({
                    "google_access_token": new_encrypted,
                }).eq("id", user["company_id"]).execute()
                cfg["google_access_token"] = updated_creds["token"]
                google_creds["token"] = updated_creds["token"]
                log.info("Google access token refreshed for company %s", user["company_id"])
        except Exception as exc:
            log.warning("Google token refresh failed (continuing with existing token): %s", exc)

        cfg["gmail_credentials"] = google_creds

    # Fill in app-level credentials
    cfg.setdefault("qbo_client_id", settings.intuit_client_id)
    cfg.setdefault("qbo_client_secret", settings.intuit_client_secret)
    cfg.setdefault("conductor_api_key", settings.conductor_api_key)

    adapter = UnifiedDataAdapter(cfg)
    adapter.set_db(db)
    return adapter
