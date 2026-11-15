"""Fail-closed, compare-and-swap persistence for rotating QBO OAuth tokens.

Every refresh path must use the *ciphertext originally read from the company
row* as its optimistic concurrency version. Encryption is randomized, so
re-encrypting a plaintext token cannot produce a usable comparison value.
"""

from datetime import datetime, timedelta, timezone

from app.utils.encryption import encrypt


class QBOInvalidGrant(RuntimeError):
    """Intuit rejected the rotating credential; never include its response."""


class QBOReconnectRequired(RuntimeError):
    """User must reconnect QuickBooks before further accounting reads."""

_QBO_DB_TIMEOUT_SECONDS = 10


def create_qbo_client(settings):
    """Give synchronous QBO DB operations a bounded PostgREST timeout."""
    from supabase import create_client
    from supabase.lib.client_options import ClientOptions

    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=ClientOptions(postgrest_client_timeout=_QBO_DB_TIMEOUT_SECONDS),
    )


def persist_rotated_qbo_tokens(
    db,
    *,
    company_id: str,
    expected_refresh_ciphertext: str,
    access_token: str,
    refresh_token: str,
    secret_key: str,
    expires_in: int = 3600,
    realm_id: str | None = None,
) -> str:
    """Persist both rotated credentials atomically, or raise on a stale row.

    Restrict the update to the exact credential read before calling Intuit.
    In particular, an in-flight refresh cannot overwrite a newer OAuth
    reconnect, disconnect, or another successfully persisted refresh.
    Return the newly stored refresh-token ciphertext for subsequent CAS calls.
    """
    if not company_id or not expected_refresh_ciphertext:
        raise RuntimeError("QBO token persistence requires a company and prior credential")

    now = datetime.now(timezone.utc)
    encrypted_refresh = encrypt(refresh_token, secret_key)
    query = (
        db.table("companies")
        .update({
            "qbo_access_token": encrypt(access_token, secret_key),
            "qbo_refresh_token": encrypted_refresh,
            "qbo_token_refreshed_at": now.isoformat(),
            "qbo_token_expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        })
        .eq("id", company_id)
        .eq("qbo_refresh_token", expected_refresh_ciphertext)
        .eq("qb_connection_status", "connected")
        .eq("qb_type", "online")
    )
    if realm_id:
        query = query.eq("qbo_realm_id", realm_id)

    # Pinned supabase-py / postgrest-py returns updated rows by default.
    # SyncFilterRequestBuilder has no .select() method after .update();
    # calling it aborts persistence, including a successful OAuth reconnect.
    result = query.execute()
    if not getattr(result, "data", None):
        raise RuntimeError("QBO token persistence matched no current connected company")
    return encrypted_refresh


def mark_qbo_needs_reconnect(
    db, *, company_id: str, expected_refresh_ciphertext: str,
    realm_id: str | None = None,
) -> None:
    """CAS transition to the already-supported disconnected status.

    Clear rejected credentials and fence a concurrent OAuth callback. A newer
    reconnect or successful rotation may never be overwritten by this path.
    """
    if not company_id or not expected_refresh_ciphertext:
        raise RuntimeError("QBO reconnect transition requires an original credential")
    query = (
        db.table("companies")
        .update({
            "qb_connection_status": "disconnected",
            "qbo_access_token": None,
            "qbo_refresh_token": None,
            "qbo_token_expires_at": None,
            "qbo_token_refreshed_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", company_id)
        .eq("qbo_refresh_token", expected_refresh_ciphertext)
        .eq("qb_connection_status", "connected")
        .eq("qb_type", "online")
    )
    if realm_id:
        query = query.eq("qbo_realm_id", realm_id)
    result = query.execute()
    if len(getattr(result, "data", None) or []) != 1:
        raise RuntimeError("QBO reconnect transition did not match the expected connection")
