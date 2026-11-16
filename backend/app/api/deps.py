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
        cfg["_qbo_expected_refresh_ciphertext"] = cfg["qbo_refresh_token"]
        cfg["qbo_refresh_token"] = decrypt(cfg["qbo_refresh_token"], settings.secret_key)
    if cfg.get("google_access_token"):
        cfg["google_access_token"] = decrypt(cfg["google_access_token"], settings.secret_key)
    if cfg.get("google_refresh_token"):
        cfg["google_refresh_token"] = decrypt(cfg["google_refresh_token"], settings.secret_key)
    # Outlook rotation persists encrypted tokens; both must be decrypted before
    # UnifiedDataAdapter constructs the Graph connector. The client secret is
    # stored encrypted too and is sent to Microsoft on every token refresh.
    if cfg.get("ms_access_token"):
        cfg["ms_access_token"] = decrypt(cfg["ms_access_token"], settings.secret_key)
    if cfg.get("ms_refresh_token"):
        cfg["_ms_expected_refresh_ciphertext"] = cfg["ms_refresh_token"]
        cfg["ms_refresh_token"] = decrypt(cfg["ms_refresh_token"], settings.secret_key)
    if cfg.get("ms_client_secret"):
        cfg["ms_client_secret"] = decrypt(cfg["ms_client_secret"], settings.secret_key)
    cfg["ms_client_id"] = cfg.get("ms_client_id") or getattr(settings, "ms_client_id", "")
    cfg["ms_client_secret"] = cfg.get("ms_client_secret") or getattr(settings, "ms_client_secret", "")

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


def get_qbo_ar_preview_adapter(*, user: dict, db):
    """Build only the tenant's verified QBO connector for staging AR acceptance.

    Unlike the general-purpose adapter dependency, this deliberately skips
    Gmail OAuth refresh and omits all email, messaging, and other employees'
    connector credentials. QBO OAuth token rotation can still persist as
    necessary to execute a read-only provider query.
    """
    from app.connectors.unified_adapter import UnifiedDataAdapter

    company_id = user.get("company_id") if isinstance(user, dict) else None
    if not isinstance(company_id, str) or not company_id:
        raise PermissionError("AR preview company identity not verified")
    company = _load_company(company_id, db)
    if (not isinstance(company, dict) or company.get("id") != company_id
            or company.get("qb_type") != "online"):
        raise PermissionError("AR preview QBO connection not verified")

    settings = get_settings()
    access_ciphertext = company.get("qbo_access_token")
    refresh_ciphertext = company.get("qbo_refresh_token")
    realm_id = company.get("qbo_realm_id")
    client_id = company.get("qbo_client_id") or settings.intuit_client_id
    client_secret = company.get("qbo_client_secret") or settings.intuit_client_secret
    if any(not isinstance(value, str) or not value.strip()
           for value in (
               access_ciphertext, refresh_ciphertext, realm_id,
               client_id, client_secret,
           )):
        raise PermissionError("AR preview QBO credentials not verified")

    access_token = decrypt(access_ciphertext, settings.secret_key)
    refresh_token = decrypt(refresh_ciphertext, settings.secret_key)
    if (not isinstance(access_token, str) or not access_token
            or not isinstance(refresh_token, str) or not refresh_token):
        raise PermissionError("AR preview QBO credentials could not be decoded")

    # Only these QBO keys cross into UnifiedDataAdapter. In particular no
    # gmail_credentials, ms_access_token or other unrelated integration token
    # can trigger work as a side effect of AR finance acceptance.
    qb_only_config = {
        "id": company_id,
        "company_id": company_id,
        "qb_type": "online",
        "qbo_client_id": client_id,
        "qbo_client_secret": client_secret,
        "qbo_access_token": access_token,
        "qbo_refresh_token": refresh_token,
        "_qbo_expected_refresh_ciphertext": refresh_ciphertext,
        "qbo_realm_id": realm_id,
    }
    adapter = UnifiedDataAdapter(qb_only_config)
    adapter.set_db(db)
    return adapter
