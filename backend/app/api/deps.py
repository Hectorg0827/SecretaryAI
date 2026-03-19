"""
FastAPI dependencies shared across API routes.

  get_db()      → Supabase client (service-role)
  get_adapter() → UnifiedDataAdapter built from the current user's company row
"""
import logging
from functools import lru_cache

from fastapi import Depends, HTTPException

from app.auth.rbac import get_current_user
from app.config import get_settings
from app.utils.encryption import decrypt

log = logging.getLogger(__name__)


def get_db():
    """Return a Supabase service-role client."""
    from supabase import create_client
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


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

    # Fill in app-level credentials
    cfg.setdefault("qbo_client_id", settings.intuit_client_id)
    cfg.setdefault("qbo_client_secret", settings.intuit_client_secret)
    cfg.setdefault("conductor_api_key", settings.conductor_api_key)

    return UnifiedDataAdapter(cfg)
