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
    # Never pass legacy company-wide mailbox tokens into an employee request.
    # They have no verified employee owner. Separate QBO/company credentials
    # from personal Gmail/Outlook credentials before connector construction.
    for key in (
        "google_access_token", "google_refresh_token", "gmail_credentials",
        "google_credentials", "ms_access_token", "ms_refresh_token",
        "_ms_expected_refresh_ciphertext", "ms_tenant_id", "ms_client_secret",
        "_private_mailbox_owner_id",
    ):
        cfg.pop(key, None)

    # Fail closed if personal-connection storage is unavailable or not migrated.
    # A manager/owner has exactly the same personal-mailbox restrictions.
    user_id = str(user.get("sub") or "")
    if (user_id and str(getattr(settings, "secretaryai_deployment_env", "")).strip().lower() == "staging"
            and bool(getattr(settings, "lola_private_intelligence_enabled", False))):
        try:
            rows = (db.table("employee_integrations")
                .select("access_token,refresh_token")
                .eq("company_id", user["company_id"])
                .eq("user_id", user_id)
                .eq("provider", "gmail")
                .limit(1).execute()).data or []
            personal = rows[0] if rows else {}
            if personal.get("access_token") and personal.get("refresh_token"):
                google_creds = {
                    "token": decrypt(personal["access_token"], settings.secret_key),
                    "refresh_token": decrypt(personal["refresh_token"], settings.secret_key),
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
                from app.auth.oauth import refresh_google_token_if_needed
                updated, was_refreshed = refresh_google_token_if_needed(google_creds)
                if was_refreshed:
                    update = (db.table("employee_integrations")
                        .update({"access_token": encrypt(updated["token"], settings.secret_key)})
                        .eq("company_id", user["company_id"]).eq("user_id", user_id)
                        .eq("provider", "gmail")
                        .eq("refresh_token", personal["refresh_token"]).execute())
                    if not update.data:
                        raise RuntimeError("Employee Gmail credential changed during refresh")
                    google_creds = updated
                cfg["gmail_credentials"] = google_creds
                cfg["_private_mailbox_owner_id"] = user_id
        except Exception:
            # Do not fall back to a colleague's/company's OAuth credentials.
            log.warning("Employee mailbox unavailable; refusing shared-credential fallback")
            cfg.pop("gmail_credentials", None)
            cfg.pop("_private_mailbox_owner_id", None)

    # Outlook remains disabled for personal use pending its user-scoped OAuth
    # and rotation migration. Existing company-scoped Outlook is not private.
    # Fill in app-level credentials
    cfg.setdefault("qbo_client_id", settings.intuit_client_id)
    cfg.setdefault("qbo_client_secret", settings.intuit_client_secret)
    cfg.setdefault("conductor_api_key", settings.conductor_api_key)

    adapter = UnifiedDataAdapter(cfg)
    adapter.set_db(db)
    return adapter
