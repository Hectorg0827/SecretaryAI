"""
QBO OAuth 2.0 + local auth routes.

GET  /auth/qbo/connect       → redirect to Intuit consent screen
GET  /auth/qbo/callback      → exchange code, store encrypted tokens, redirect to app
DELETE /auth/qbo/disconnect  → revoke token, clear DB fields
POST /auth/login             → local email/password login, returns JWT
POST /auth/refresh           → refresh JWT
"""
import secrets
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.oauth import (
    build_authorization_url, exchange_code_for_tokens, revoke_token,
    build_google_authorization_url, exchange_google_code_for_tokens, revoke_google_token,
)
from app.auth.jwt import create_access_token, decode_access_token, verify_password, hash_password
from app.auth.rbac import get_current_user
from app.config import get_settings
from app.utils.encryption import encrypt
from app.utils.rate_limiter import login_limiter, require_rate_limit

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()

# ─── Redis-backed OAuth state store ───────────────────────────────────────────
_OAUTH_STATE_TTL = 600  # 10 minutes


def _redis():
    import redis as _redis_lib
    return _redis_lib.from_url(settings.redis_url, decode_responses=True)


def _state_set(state: str, company_id: str) -> None:
    try:
        r = _redis()
        r.setex(f"oauth_state:{state}", _OAUTH_STATE_TTL, company_id)
    except Exception as exc:
        log.warning("Redis unavailable for OAuth state; using fallback: %s", exc)
        _oauth_states_fallback[state] = company_id


def _state_pop(state: str) -> str | None:
    try:
        r = _redis()
        key = f"oauth_state:{state}"
        company_id = r.get(key)
        if company_id:
            r.delete(key)
        return company_id
    except Exception:
        return _oauth_states_fallback.pop(state, None)


# Fallback for when Redis is unreachable (single-worker dev only)
_oauth_states_fallback: dict[str, str] = {}


# ─── QBO OAuth ────────────────────────────────────────────────────────────────

@router.get("/qbo/connect-url")
async def qbo_connect_url(user: dict = Depends(get_current_user)):
    """Return the QBO OAuth consent URL as JSON (frontend handles the redirect)."""
    state = secrets.token_urlsafe(32)
    _state_set(state, user["company_id"])
    url = build_authorization_url(state)
    return {"url": url}


@router.get("/qbo/connect")
async def qbo_connect(user: dict = Depends(get_current_user)):
    """Initiate QBO OAuth 2.0 — redirect to Intuit consent screen."""
    state = secrets.token_urlsafe(32)
    _state_set(state, user["company_id"])
    url = build_authorization_url(state)
    return RedirectResponse(url=url)


@router.get("/qbo/callback")
async def qbo_callback(
    code: Annotated[str, Query()] = "",
    state: Annotated[str, Query()] = "",
    realm_id: Annotated[str, Query(alias="realmId")] = "",
    error: Annotated[str, Query()] = "",
):
    """
    Intuit redirects here after the user approves or denies access.

    On success: exchange code for tokens, encrypt them, persist to Supabase,
    then redirect the user to the desktop/web app.
    On error: redirect to an error page.
    """
    if error:
        log.warning("QBO OAuth error: %s", error)
        return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=error&reason={error}")

    company_id = _state_pop(state)
    if not company_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    if not code or not realm_id:
        raise HTTPException(status_code=400, detail="Missing code or realmId from Intuit")

    # Exchange authorization code for tokens
    try:
        tokens = await exchange_code_for_tokens(code, realm_id)
    except Exception as exc:
        log.error("QBO token exchange failed: %s", exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=error&reason=token_exchange")

    # Encrypt tokens before storing
    encrypted_access = encrypt(tokens["access_token"], settings.secret_key)
    encrypted_refresh = encrypt(tokens["refresh_token"], settings.secret_key)

    # Persist to Supabase (service-role key bypasses RLS)
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        db.table("companies").update({
            "qb_type": "online",
            "qb_connection_status": "connected",
            "qbo_realm_id": realm_id,
            "qbo_access_token": encrypted_access,
            "qbo_refresh_token": encrypted_refresh,
            "qbo_connected_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", company_id).execute()
        log.info("QBO tokens stored for company %s (realm %s)", company_id, realm_id)
    except Exception as exc:
        log.error("Failed to store QBO tokens for company %s: %s", company_id, exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=error&reason=db_write")

    return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=connected")


@router.delete("/qbo/disconnect")
async def qbo_disconnect(user: dict = Depends(get_current_user)):
    """Revoke QBO tokens and clear connection in DB."""
    company_id = user["company_id"]

    try:
        from supabase import create_client
        from app.utils.encryption import decrypt
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = db.table("companies").select("qbo_refresh_token").eq("id", company_id).execute()
        company = result.data[0] if result.data else {}

        if company.get("qbo_refresh_token"):
            token = decrypt(company["qbo_refresh_token"], settings.secret_key)
            await revoke_token(token)

        db.table("companies").update({
            "qb_connection_status": "disconnected",
            "qbo_access_token": None,
            "qbo_refresh_token": None,
            "qbo_realm_id": None,
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("QBO disconnect failed for %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to disconnect QuickBooks")

    return {"status": "disconnected"}


# ─── Google OAuth (Gmail + Sheets) ───────────────────────────────────────────

@router.get("/gmail/connect-url")
async def gmail_connect_url(user: dict = Depends(get_current_user)):
    """Return the Google OAuth consent URL as JSON (frontend handles the redirect)."""
    state = secrets.token_urlsafe(32)
    _state_set(state, user["company_id"])
    url = build_google_authorization_url(state)
    return {"url": url}


@router.get("/gmail/callback")
async def gmail_callback(
    code: Annotated[str, Query()] = "",
    state: Annotated[str, Query()] = "",
    error: Annotated[str, Query()] = "",
):
    """
    Google redirects here after the user approves or denies access.
    Stores encrypted access + refresh tokens, then redirects back to the app.
    """
    if error:
        log.warning("Google OAuth error: %s", error)
        return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=error&reason={error}")

    company_id = _state_pop(state)
    if not company_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code from Google")

    try:
        tokens = await exchange_google_code_for_tokens(code)
    except Exception as exc:
        log.error("Google token exchange failed: %s", exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=error&reason=token_exchange")

    encrypted_access = encrypt(tokens["access_token"], settings.secret_key)
    encrypted_refresh = encrypt(tokens["refresh_token"], settings.secret_key) if tokens["refresh_token"] else None

    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        db.table("companies").update({
            "google_access_token":  encrypted_access,
            "google_refresh_token": encrypted_refresh,
            "google_connected_at":  datetime.now(timezone.utc).isoformat(),
        }).eq("id", company_id).execute()
        log.info("Google tokens stored for company %s", company_id)
    except Exception as exc:
        log.error("Failed to store Google tokens for company %s: %s", company_id, exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=error&reason=db_write")

    return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=connected")


@router.delete("/gmail/disconnect")
async def gmail_disconnect(user: dict = Depends(get_current_user)):
    """Revoke Google tokens and clear connection in DB."""
    company_id = user["company_id"]

    try:
        from supabase import create_client
        from app.utils.encryption import decrypt
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = db.table("companies").select("google_access_token").eq("id", company_id).execute()
        company = result.data[0] if result.data else {}

        if company.get("google_access_token"):
            token = decrypt(company["google_access_token"], settings.secret_key)
            await revoke_google_token(token)

        db.table("companies").update({
            "google_access_token":  None,
            "google_refresh_token": None,
            "google_connected_at":  None,
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("Google disconnect failed for %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to disconnect Google")

    return {"status": "disconnected"}


# ─── Local auth ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, _=Depends(require_rate_limit(login_limiter))):
    """
    Authenticate with email + password.
    Returns a short-lived JWT (access token) and company_id.
    """
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = (
            db.table("users")
            .select("id, company_id, role, password_hash, is_active")
            .eq("email", body.email.lower().strip())
            .execute()
        )
    except Exception as exc:
        log.error("Login DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    users = result.data or []
    if not users or not verify_password(body.password, users[0]["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = users[0]
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")

    token = create_access_token({
        "sub": user["id"],
        "company_id": user["company_id"],
        "role": user["role"],
    })
    return {
        "access_token": token,
        "token_type": "bearer",
        "company_id": user["company_id"],
        "role": user["role"],
    }


class RefreshRequest(BaseModel):
    access_token: str  # the current (possibly expired) token — we re-sign it


@router.post("/refresh")
async def refresh_token(body: RefreshRequest):
    """
    Issue a new JWT from a valid-but-expiring token.
    We decode without checking expiry, verify the user still exists and is active,
    then issue a fresh access token.
    """
    from jose import jwt as jose_jwt, JWTError
    try:
        # Decode WITHOUT verifying expiry so a just-expired token still works
        payload = jose_jwt.decode(
            body.access_token,
            settings.secret_key,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.get("sub")
    company_id = payload.get("company_id")
    role = payload.get("role")
    if not user_id or not company_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Verify the user still exists and is active
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = db.table("users").select("is_active").eq("id", user_id).execute()
    except Exception as exc:
        log.error("Refresh token DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data or not result.data[0].get("is_active", True):
        raise HTTPException(status_code=401, detail="User not found or disabled")

    new_token = create_access_token({"sub": user_id, "company_id": company_id, "role": role})
    return {"access_token": new_token, "token_type": "bearer"}


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return the current user's profile (id, name, email, role)."""
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = (
            db.table("users")
            .select("id, name, email, role")
            .eq("id", user["sub"])
            .execute()
        )
    except Exception as exc:
        log.error("GET /auth/me DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    return result.data[0]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _app_base_url() -> str:
    if settings.frontend_url:
        return settings.frontend_url.rstrip("/")
    if settings.debug:
        return "http://localhost:5173"
    return "https://app.secretaryai.com"
