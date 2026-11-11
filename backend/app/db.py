"""Shared Supabase client dependency without importing authentication modules.

Keeping this dependency outside the API/auth modules lets both authorization
and business routes reuse the same pooled client. FastAPI also caches the
dependency result within one request, so the session-stamp check and route
handler share the client.
"""

from app.config import get_settings

_db_client = None


def get_db():
    """Return the module-level Supabase service-role client (pooled)."""
    global _db_client
    if _db_client is None:
        from supabase import create_client
        settings = get_settings()
        _db_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _db_client
