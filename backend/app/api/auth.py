"""
QBO OAuth 2.0 + local auth routes.

GET  /auth/qbo/connect       → redirect to Intuit consent screen
GET  /auth/qbo/callback      → exchange code, store encrypted tokens, redirect to app
DELETE /auth/qbo/disconnect  → revoke token, clear DB fields
POST /auth/login             → local email/password login, returns JWT
POST /auth/refresh           → refresh JWT
POST /auth/register          → create company + owner user, returns JWT
POST /auth/2fa/setup         → generate TOTP secret, store pending in Redis
POST /auth/2fa/verify        → verify TOTP code, enable 2FA in DB
DELETE /auth/2fa/disable     → verify code, disable 2FA in DB
"""
import secrets
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
import re

from app.auth.oauth import (
    build_authorization_url, exchange_code_for_tokens, revoke_token as revoke_qbo_token,
    build_google_authorization_url, exchange_google_code_for_tokens, revoke_google_token,
)
from app.auth.jwt import (
    AuthStoreUnavailable, claim_jti_once, create_access_token, decode_access_token,
    hash_password, is_session_superseded, revoke_token as revoke_session_token,
    revoke_user_sessions, verify_password,
)
from app.auth.rbac import get_current_user, oauth2_scheme, require_permission
from app.config import get_settings
from app.utils.encryption import encrypt, decrypt
from app.utils.rate_limiter import (
    account_link_limiter, login_limiter, login_email_limiter, password_reset_limiter,
    require_rate_limit, totp_limiter,
)

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()

# ─── Redis-backed OAuth state store ───────────────────────────────────────────
_OAUTH_STATE_TTL = 600  # 10 minutes


def _redis():
    import redis as _redis_lib
    return _redis_lib.from_url(settings.redis_url, decode_responses=True)


def _state_set(state: str, company_id: str) -> None:
    """Remember which company started an OAuth connect (10 minutes)."""
    try:
        _redis().setex(f"oauth_state:{state}", _OAUTH_STATE_TTL, company_id)
    except Exception as exc:
        # No per-process fallback: with more than one server process the
        # callback would land on a worker that never saw the state.
        log.error("Redis unavailable for OAuth state: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable. Please try again.")


def _state_pop(state: str) -> str | None:
    """Return the company for an OAuth state and delete it (single use)."""
    if not state:
        return None
    try:
        r = _redis()
        key = f"oauth_state:{state}"
        pipe = r.pipeline()
        pipe.get(key)
        pipe.delete(key)
        company_id, _ = pipe.execute()
        return company_id
    except Exception as exc:
        log.error("Redis unavailable for OAuth state lookup: %s", exc)
        return None


def _audit(company_id: str, event_type: str, actor_id: str | None = None, **metadata) -> None:
    """Best-effort audit record for auth / integration events."""
    try:
        from app.api.deps import get_db
        from app.utils.audit import write_audit_event
        write_audit_event(get_db(), company_id=company_id, event_type=event_type,
                          actor_id=actor_id, metadata=metadata or None)
    except Exception as exc:
        log.error("Auth audit failed for %s: %s", event_type, exc)


# ─── QBO OAuth ────────────────────────────────────────────────────────────────

@router.get("/qbo/connect-url")
async def qbo_connect_url(user: dict = Depends(require_permission("manage_integrations"))):
    """Return the QBO OAuth consent URL as JSON (frontend handles the redirect)."""
    state = secrets.token_urlsafe(32)
    _state_set(state, user["company_id"])
    url = build_authorization_url(state)
    return {"url": url}


@router.get("/qbo/connect")
async def qbo_connect(user: dict = Depends(require_permission("manage_integrations"))):
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
        return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=error&reason={_safe_reason(error)}")

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

    from datetime import timedelta
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))
    ).isoformat()

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
            "qbo_token_expires_at": expires_at,
        }).eq("id", company_id).execute()
        log.info("QBO tokens stored for company %s (realm %s)", company_id, realm_id)
        _audit(company_id, "integration_connected", None, integration="quickbooks_online", realm_id=realm_id)
    except Exception as exc:
        log.error("Failed to store QBO tokens for company %s: %s", company_id, exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=error&reason=db_write")

    return RedirectResponse(url=f"{_app_base_url()}/settings?qbo=connected")


@router.delete("/qbo/disconnect")
async def qbo_disconnect(user: dict = Depends(require_permission("manage_integrations"))):
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
            await revoke_qbo_token(token)

        db.table("companies").update({
            "qb_connection_status": "disconnected",
            "qbo_access_token": None,
            "qbo_refresh_token": None,
            "qbo_realm_id": None,
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("QBO disconnect failed for %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to disconnect QuickBooks")

    _audit(company_id, "integration_disconnected", user.get("sub"), integration="quickbooks_online")
    return {"status": "disconnected"}


# ─── Google OAuth (Gmail + Sheets) ───────────────────────────────────────────

@router.get("/gmail/connect-url")
async def gmail_connect_url(user: dict = Depends(require_permission("manage_integrations"))):
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
        return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=error&reason={_safe_reason(error)}")

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
        _audit(company_id, "integration_connected", None, integration="google")
    except Exception as exc:
        log.error("Failed to store Google tokens for company %s: %s", company_id, exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=error&reason=db_write")

    return RedirectResponse(url=f"{_app_base_url()}/settings?gmail=connected")


@router.delete("/gmail/disconnect")
async def gmail_disconnect(user: dict = Depends(require_permission("manage_integrations"))):
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

    _audit(company_id, "integration_disconnected", user.get("sub"), integration="google")
    return {"status": "disconnected"}


# ─── Microsoft OAuth (Outlook mail) ───────────────────────────────────────────

@router.get("/outlook/connect-url")
async def outlook_connect_url(user: dict = Depends(require_permission("manage_integrations"))):
    """Return the Microsoft sign-in URL for connecting an Outlook mailbox."""
    from app.auth.oauth import build_ms_authorization_url, microsoft_configured
    if not microsoft_configured():
        raise HTTPException(status_code=503, detail="Outlook connection is not set up on this server yet.")
    state = secrets.token_urlsafe(32)
    _state_set(f"ms:{state}", user["company_id"])
    return {"url": build_ms_authorization_url(state)}


@router.get("/outlook/callback")
async def outlook_callback(
    code: Annotated[str, Query()] = "",
    state: Annotated[str, Query()] = "",
    error: Annotated[str, Query()] = "",
):
    """
    Microsoft redirects here after the user approves or declines.
    The company is taken from the single-use state created by connect-url —
    never from anything in this request.
    """
    from datetime import timedelta
    from app.auth.oauth import exchange_ms_code_for_tokens

    if error:
        log.warning("Microsoft OAuth error: %s", _safe_reason(error))
        return RedirectResponse(url=f"{_app_base_url()}/settings?outlook=error&reason={_safe_reason(error)}")

    company_id = _state_pop(f"ms:{state}") if state else None
    if not company_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code from Microsoft")

    try:
        tokens = await exchange_ms_code_for_tokens(code)
    except Exception as exc:
        log.error("Microsoft token exchange failed: %s", type(exc).__name__)
        return RedirectResponse(url=f"{_app_base_url()}/settings?outlook=error&reason=token_exchange")

    if not tokens.get("refresh_token"):
        # Without it the connection would die within the hour.
        return RedirectResponse(url=f"{_app_base_url()}/settings?outlook=error&reason=no_offline_access")

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])).isoformat()
    try:
        _db().table("companies").update({
            "ms_tenant_id": settings.ms_tenant or "common",
            "ms_access_token": encrypt(tokens["access_token"], settings.secret_key),
            "ms_refresh_token": encrypt(tokens["refresh_token"], settings.secret_key),
            "ms_token_expires_at": expires_at,
            "ms_connected_at": datetime.now(timezone.utc).isoformat(),
            "ms_account_email": tokens.get("account_email") or None,
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("Failed to store Microsoft tokens for company %s: %s", company_id, exc)
        return RedirectResponse(url=f"{_app_base_url()}/settings?outlook=error&reason=db_write")

    _audit(company_id, "integration_connected", None, integration="outlook",
           account=tokens.get("account_email") or "")
    return RedirectResponse(url=f"{_app_base_url()}/settings?outlook=connected")


@router.delete("/outlook/disconnect")
async def outlook_disconnect(user: dict = Depends(require_permission("manage_integrations"))):
    """Forget this company's Outlook tokens."""
    company_id = user["company_id"]
    try:
        _db().table("companies").update({
            "ms_access_token": None,
            "ms_refresh_token": None,
            "ms_token_expires_at": None,
            "ms_connected_at": None,
            "ms_account_email": None,
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("Outlook disconnect failed for %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to disconnect Outlook")
    _audit(company_id, "integration_disconnected", user.get("sub"), integration="outlook")
    return {"status": "disconnected"}


# ─── Local auth ───────────────────────────────────────────────────────────────

def _verify_totp(user_id: str, secret: str, code: str) -> None:
    """
    Check a 6-digit authenticator code. Raises HTTPException on any failure.

    * Guessing is throttled per account (not per IP), so a 6-digit code
      cannot be brute-forced from many addresses.
    * A code is accepted once: a code seen over someone's shoulder or captured
      in transit cannot be replayed inside its validity window.
    """
    import pyotp

    code = (code or "").strip().replace(" ", "")
    if not totp_limiter.is_allowed(str(user_id)):
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect codes. Wait a few minutes and try again.",
            headers={"Retry-After": str(totp_limiter.window_seconds)},
        )
    if not (code.isdigit() and len(code) == 6) or not pyotp.TOTP(secret).verify(code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid 2FA code")
    try:
        first_use = _redis().set(f"totp_used:{user_id}:{code}", "1", nx=True, ex=120)
    except Exception as exc:
        log.error("2FA replay check unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if not first_use:
        raise HTTPException(status_code=401, detail="That code was already used. Wait for the next one.")


def _login_response(user: dict) -> dict:
    token = create_access_token({
        "sub": user["id"],
        "company_id": user["company_id"],
        "role": user["role"],
    })
    return {"access_token": token, "token_type": "bearer",
            "company_id": user["company_id"], "role": user["role"]}


# A real bcrypt hash of a random value, used only to equalise login timing.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


class LoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    totp_code: str | None = None
    pre_auth_token: str | None = None  # mobile 2FA second step

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError("Invalid email address")
        return v


@router.post("/login")
async def login(body: LoginRequest, _=Depends(require_rate_limit(login_limiter))):
    """
    Authenticate with email + password, optionally with a TOTP code.

    Two-step mobile flow: first call returns requires_2fa + pre_auth_token;
    second call sends pre_auth_token + totp_code to complete authentication.
    """
    from supabase import create_client
    db = create_client(settings.supabase_url, settings.supabase_service_role_key)

    # ── Second-step: pre_auth_token + totp_code (mobile 2FA) ─────────────────
    if body.pre_auth_token and body.totp_code:
        from jose import jwt as jose_jwt, JWTError
        try:
            payload = jose_jwt.decode(
                body.pre_auth_token,
                settings.secret_key,
                algorithms=["HS256"],
            )
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid or expired 2FA session")

        if payload.get("scope") != "2fa_pending":
            raise HTTPException(status_code=401, detail="Invalid token scope")

        user_id = payload.get("sub")
        try:
            result = (
                db.table("users")
                .select("id, company_id, role, is_active, totp_enabled, totp_secret")
                .eq("id", user_id)
                .execute()
            )
        except Exception as exc:
            log.error("2FA step-2 DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")

        if not result.data:
            raise HTTPException(status_code=401, detail="User not found")

        user = result.data[0]
        if not user.get("is_active", True):
            raise HTTPException(status_code=403, detail="Account disabled")
        if not user.get("totp_enabled") or not user.get("totp_secret"):
            raise HTTPException(status_code=400, detail="2FA not configured")

        try:
            secret = decrypt(user["totp_secret"], settings.secret_key)
        except Exception:
            raise HTTPException(status_code=500, detail="2FA configuration error")

        _verify_totp(user["id"], secret, body.totp_code)
        _audit(user["company_id"], "user_login", user["id"], role=user["role"], two_factor=True)
        return _login_response(user)

    # ── First-step: email + password ──────────────────────────────────────────
    if not body.email or not body.password:
        raise HTTPException(status_code=422, detail="email and password are required")

    # Per-account throttle (in addition to the per-IP dependency above). The IP
    # limiter is only meaningful when the proxy's forwarded headers are trusted;
    # this one holds regardless of where the attempts come from.
    if not login_email_limiter.is_allowed(body.email.lower().strip()):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts for this account. Try again in a few minutes.",
            headers={"Retry-After": str(login_email_limiter.window_seconds)},
        )

    try:
        result = (
            db.table("users")
            .select("id, company_id, role, password_hash, is_active, totp_enabled, totp_secret, email_verified_at")
            .eq("email", body.email.lower().strip())
            .execute()
        )
    except Exception as exc:
        log.error("Login DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    users = result.data or []
    # An invited user who has not accepted yet has no password: never matches.
    if not users or not verify_password(body.password, users[0].get("password_hash") or ""):
        if not users:
            # Spend the same time as a real check so response time does not
            # reveal whether the email exists.
            verify_password(body.password, _DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = users[0]
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")
    if settings.require_email_verification and not user.get("email_verified_at"):
        raise HTTPException(
            status_code=403,
            detail="Please confirm your email address first. Check your inbox for the confirmation link.",
        )

    # ── 2FA enforcement ──────────────────────────────────────────────────────
    if user.get("totp_enabled") and user.get("totp_secret"):
        if not body.totp_code:
            # Password correct but 2FA required — return a short-lived pre-auth
            # token so the frontend can prompt for TOTP without re-sending password.
            from datetime import timedelta
            pre_auth = create_access_token(
                {"sub": user["id"], "scope": "2fa_pending"},
                expires_delta=timedelta(minutes=5),
            )
            return {
                "requires_2fa": True,
                "pre_auth_token": pre_auth,
            }
        # TOTP code supplied inline (web flow) — verify it
        try:
            secret = decrypt(user["totp_secret"], settings.secret_key)
        except Exception:
            raise HTTPException(status_code=500, detail="2FA configuration error")
        _verify_totp(user["id"], secret, body.totp_code)

    token = create_access_token({
        "sub": user["id"],
        "company_id": user["company_id"],
        "role": user["role"],
    })

    try:
        db.table("users").update(
            {"last_login_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user["id"]).execute()
    except Exception:
        pass

    # Audit successful login (non-fatal)
    try:
        from app.utils.audit import write_audit_event
        write_audit_event(
            db,
            company_id=user["company_id"],
            event_type="user_login",
            actor_id=user["id"],
            metadata={"role": user["role"]},
        )
    except Exception:
        pass

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
    Issue a new JWT from a recently-valid token, with bounded renewal, revocation
    enforcement, and single-use rotation.

    Security properties:
    - A revoked token (logged out, or already rotated) cannot be refreshed.
    - Renewal is bounded: a token expired longer than the refresh window can NOT
      be renewed — the user must log in again. This prevents an old leaked token
      from being renewed indefinitely.
    - Refresh is single-use: the presented token's JTI is blacklisted when a new
      token is issued, so a captured token can't be replayed for a second refresh
      (reuse detection).
    """
    from jose import jwt as jose_jwt, JWTError
    from app.auth.jwt import is_token_revoked

    try:
        # Decode WITHOUT verifying expiry so a just-expired token can still be
        # exchanged — bounded explicitly below.
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
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not user_id or not company_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Only a user-session token can be refreshed into a user-session token.
    if payload.get("scope", "access") != "access":
        raise HTTPException(status_code=401, detail="Token is not refreshable")

    # Enforce revocation (logout / prior rotation / removed user).
    try:
        if jti and is_token_revoked(jti):
            raise HTTPException(status_code=401, detail="Token revoked; please log in again")
        if is_session_superseded(payload):
            raise HTTPException(status_code=401, detail="Session ended; please log in again")
    except AuthStoreUnavailable:
        raise HTTPException(status_code=503, detail="Sign-in service temporarily unavailable. Please try again shortly.")

    # Bound the renewal window. A token expired for longer than the refresh
    # window cannot be renewed — this caps the lifetime of a leaked token.
    refresh_window_seconds = settings.refresh_token_expire_days * 86400
    if exp is not None:
        expired_for = datetime.now(timezone.utc).timestamp() - float(exp)
        if expired_for > refresh_window_seconds:
            raise HTTPException(status_code=401, detail="Session expired; please log in again")

    # Verify the user still exists and is active, and re-read role/company from
    # the DB so a demoted or moved user does not keep stale privileges for the
    # whole refresh window.
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = (
            db.table("users")
            .select("is_active, role, company_id")
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:
        log.error("Refresh token DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data or not result.data[0].get("is_active", True):
        raise HTTPException(status_code=401, detail="User not found or disabled")

    current = result.data[0]
    role = current.get("role") or role
    company_id = current.get("company_id") or company_id

    # Single-use rotation, atomically: the presented token is consumed BEFORE a
    # new one is issued, so two requests with the same token cannot both get a
    # fresh session, and if the store is down no token is issued at all.
    if jti:
        try:
            lifetime = int(refresh_window_seconds + settings.access_token_expire_minutes * 60)
            if not claim_jti_once(jti, ttl_seconds=lifetime):
                raise HTTPException(status_code=401, detail="Token already used; please log in again")
        except AuthStoreUnavailable:
            raise HTTPException(status_code=503, detail="Sign-in service temporarily unavailable. Please try again shortly.")

    new_token = create_access_token({"sub": user_id, "company_id": company_id, "role": role})
    return {"access_token": new_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(
    token: str = Depends(oauth2_scheme),
    _user: dict = Depends(get_current_user),
):
    """
    Revoke the current JWT by adding its JTI to the Redis blacklist.
    The token becomes invalid immediately, even before its natural expiry.
    """
    try:
        revoke_session_token(token)
    except AuthStoreUnavailable:
        # Be honest: the session could NOT be ended server-side.
        raise HTTPException(status_code=503, detail="Could not sign out right now. Please try again.")
    _audit(_user["company_id"], "user_logout", _user.get("sub"))
    return {"status": "logged_out"}


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return the current user's profile (id, name, email, role)."""
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = (
            db.table("users")
            .select("id, name, email, role, totp_enabled")
            .eq("id", user["sub"])
            .execute()
        )
    except Exception as exc:
        log.error("GET /auth/me DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    return result.data[0]


# ─── Registration ─────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    company_name: str
    email: str
    password: str
    full_name: str = ""

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError("Invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_new_password(v)

    @field_validator("company_name")
    @classmethod
    def validate_company_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or len(v) > 200:
            raise ValueError("Please enter your company name")
        return v


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, _=Depends(require_rate_limit(login_limiter))):
    """
    Create a new company and owner user, then return a JWT so the user is
    immediately logged in.
    """
    from supabase import create_client
    db = create_client(settings.supabase_url, settings.supabase_service_role_key)

    # Check email uniqueness
    try:
        existing = (
            db.table("users")
            .select("id")
            .eq("email", body.email)
            .execute()
        )
    except Exception as exc:
        log.error("Register email-check DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if existing.data:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create company
    try:
        company_result = (
            db.table("companies")
            .insert({"name": body.company_name})
            .execute()
        )
    except Exception as exc:
        log.error("Register company insert failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not company_result.data:
        log.error("Register company insert returned no data")
        raise HTTPException(status_code=500, detail="Failed to create company")

    company_id = company_result.data[0]["id"]

    # Create user
    user_payload: dict = {
        "email": body.email,
        "password_hash": hash_password(body.password),
        "role": "owner",
        "company_id": company_id,
        "is_active": True,
    }
    if body.full_name:
        user_payload["name"] = body.full_name

    try:
        user_result = (
            db.table("users")
            .insert(user_payload)
            .execute()
        )
    except Exception as exc:
        log.error("Register user insert failed: %s", exc)
        # Attempt to clean up orphaned company row
        try:
            db.table("companies").delete().eq("id", company_id).execute()
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not user_result.data:
        log.error("Register user insert returned no data")
        raise HTTPException(status_code=500, detail="Failed to create user")

    user = user_result.data[0]

    # Start the free trial (non-fatal)
    from app.middleware.subscription_guard import start_trial
    start_trial(db, company_id)

    # Seed per-company defaults (non-fatal — log and continue if fails)
    try:
        await _seed_company_defaults(company_id, db)
    except Exception as exc:
        log.warning("Failed to seed company defaults for %s (non-fatal): %s", company_id, exc)

    token = create_access_token({
        "sub": user["id"],
        "company_id": company_id,
        "role": "owner",
    })
    log.info("New owner registered: user=%s company=%s", user["id"], company_id)

    # Ask the new owner to confirm their email address (non-fatal).
    email_sent = False
    try:
        email_sent = send_verification_email(user)
    except Exception as exc:
        log.warning("Could not send verification email for %s: %s", user["id"], exc)

    if settings.require_email_verification:
        # No session until the address is confirmed.
        return {
            "status": "verification_required",
            "message": "Check your inbox and confirm your email address to finish signing up.",
            "email_sent": email_sent,
        }

    # Audit registration (non-fatal)
    try:
        from app.utils.audit import write_audit_event
        write_audit_event(
            db,
            company_id=company_id,
            event_type="company_registered",
            actor_id=user["id"],
            metadata={"role": "owner"},
        )
    except Exception:
        pass

    return {
        "access_token": token,
        "token_type": "bearer",
        "company_id": company_id,
        "role": "owner",
    }


async def _seed_company_defaults(company_id: str, db) -> None:
    """
    Idempotent — seeds company_features flags and default policy_rules for a
    newly registered company. Safe to call multiple times.
    """
    # 1. company_features — per-tenant feature flags
    db.table("company_features").upsert(
        {
            "company_id": company_id,
            "computer_use_enabled": False,
            "browser_auto_enabled": True,
            "require_approval_above": "500.00",
            "api_auto_sync": True,
            "file_ingestion_enabled": True,
        },
        on_conflict="company_id",
    ).execute()

    # 2. policy_rules — default allow/deny rules
    from app.domain.policy import PolicyEngine
    engine = PolicyEngine(company_id=company_id, db=db)
    await engine.seed_defaults()


# ─── Two-factor authentication ────────────────────────────────────────────────

class TwoFACodeRequest(BaseModel):
    code: str


@router.post("/2fa/setup")
async def twofa_setup(user: dict = Depends(get_current_user)):
    """
    Generate a TOTP secret for the authenticated user and store it in Redis
    pending verification.  Returns the secret and an otpauth:// URL for QR
    code generation.  Does NOT persist to DB until /2fa/verify is called.
    """
    import pyotp

    secret = pyotp.random_base32()
    user_id = user["sub"]

    # Fetch user email for the otpauth URL label
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = db.table("users").select("email").eq("id", user_id).execute()
    except Exception as exc:
        log.error("2FA setup DB query failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    email = result.data[0]["email"]

    # Store pending secret in Redis with 10-minute TTL
    try:
        r = _redis()
        r.setex(f"2fa_pending:{user_id}", 600, secret)
    except Exception as exc:
        log.error("2FA setup Redis write failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    otpauth_url = f"otpauth://totp/SecretaryAI:{email}?secret={secret}&issuer=SecretaryAI"
    return {"secret": secret, "otpauth_url": otpauth_url}


@router.post("/2fa/verify")
async def twofa_verify(body: TwoFACodeRequest, user: dict = Depends(get_current_user)):
    """
    Verify a TOTP code against the pending secret stored in Redis.
    On success, saves the encrypted secret to the DB and enables 2FA.
    """
    import pyotp

    user_id = user["sub"]

    # Retrieve pending secret from Redis
    try:
        r = _redis()
        secret = r.get(f"2fa_pending:{user_id}")
    except Exception as exc:
        log.error("2FA verify Redis read failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not secret:
        raise HTTPException(status_code=400, detail="No pending 2FA setup found; call /2fa/setup first")

    if not pyotp.TOTP(secret).verify(body.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # Persist encrypted secret and enable 2FA
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        db.table("users").update({
            "totp_secret": encrypt(secret, settings.secret_key),
            "totp_enabled": True,
        }).eq("id", user_id).execute()
    except Exception as exc:
        log.error("2FA verify DB update failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Clean up pending Redis key
    try:
        r = _redis()
        r.delete(f"2fa_pending:{user_id}")
    except Exception as exc:
        log.warning("2FA verify Redis cleanup failed for user %s: %s", user_id, exc)

    log.info("2FA enabled for user %s", user_id)
    _audit(user["company_id"], "two_factor_enabled", user_id)
    return {"enabled": True}


@router.delete("/2fa/disable")
async def twofa_disable(body: TwoFACodeRequest, user: dict = Depends(get_current_user)):
    """
    Disable 2FA for the authenticated user after verifying the current TOTP code.
    """
    import pyotp

    user_id = user["sub"]

    # Fetch current encrypted TOTP secret from DB
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = (
            db.table("users")
            .select("totp_secret, totp_enabled")
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:
        log.error("2FA disable DB query failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    row = result.data[0]
    if not row.get("totp_enabled") or not row.get("totp_secret"):
        raise HTTPException(status_code=400, detail="2FA is not enabled for this account")

    secret = decrypt(row["totp_secret"], settings.secret_key)
    _verify_totp(user_id, secret, body.code)

    # Disable 2FA in DB
    try:
        db.table("users").update({
            "totp_enabled": False,
            "totp_secret": None,
        }).eq("id", user_id).execute()
    except Exception as exc:
        log.error("2FA disable DB update failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    log.info("2FA disabled for user %s", user_id)
    _audit(user["company_id"], "two_factor_disabled", user_id)
    return {"enabled": False}


# ─── Passwords, invitations and email confirmation ────────────────────────────
#
# All three use short-lived, single-purpose, single-use links:
#   * the token carries a `scope` that get_current_user refuses, so a link can
#     never be used as a login session;
#   * it is bound to the account's current password fingerprint, so it dies
#     the moment the password changes;
#   * it is consumed atomically on use, so it cannot be replayed.

PASSWORD_RESET_MINUTES = 30
INVITE_DAYS = 7
EMAIL_VERIFY_DAYS = 3


def validate_new_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 characters")
    if not re.search(r'[A-Za-z]', v):
        raise ValueError("Password must contain at least one letter")
    if not re.search(r'[0-9]', v):
        raise ValueError("Password must contain at least one number")
    return v


def _password_fingerprint(password_hash: str | None) -> str:
    """Opaque marker of the current password, used to tie a link to it."""
    import hashlib
    return hashlib.sha256(f"{settings.secret_key}:{password_hash or ''}".encode()).hexdigest()[:24]


def make_account_link_token(user: dict, scope: str, lifetime) -> str:
    return create_access_token(
        {"sub": user["id"], "scope": scope, "pwv": _password_fingerprint(user.get("password_hash"))},
        expires_delta=lifetime,
    )


def account_link(path: str, token: str) -> str:
    return f"{_app_base_url()}{path}?token={token}"


def _db():
    from app.api.deps import get_db
    return get_db()


def _consume_account_link(token: str, scope: str) -> dict:
    """
    Validate a single-purpose link token and return the user row.
    Raises HTTPException(400) with one generic message for every failure, so
    the response never reveals why a link was refused.
    """
    bad = HTTPException(status_code=400, detail="This link is invalid or has expired. Please request a new one.")
    payload = decode_access_token(token or "")
    if not payload or payload.get("scope") != scope or not payload.get("sub") or not payload.get("jti"):
        raise bad
    try:
        result = (
            _db().table("users")
            .select("id, company_id, email, role, is_active, password_hash, email_verified_at")
            .eq("id", payload["sub"])
            .execute()
        )
    except Exception as exc:
        log.error("Account link lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if not result.data:
        raise bad
    user = result.data[0]
    if not user.get("is_active", True):
        raise bad
    if payload.get("pwv") != _password_fingerprint(user.get("password_hash")):
        raise bad
    try:
        remaining = max(60, int(payload["exp"] - datetime.now(timezone.utc).timestamp()))
        if not claim_jti_once(payload["jti"], ttl_seconds=remaining):
            raise bad
    except AuthStoreUnavailable:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable. Please try again shortly.")
    return user


class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, _=Depends(require_rate_limit(account_link_limiter))):
    """
    Email a password-reset link. Always answers the same way, whether or not
    the address has an account, so it cannot be used to discover who is a user.
    """
    from datetime import timedelta
    from app.utils.mailer import send_system_email

    generic = {"status": "ok", "message": "If that email has an account, a reset link is on its way."}
    email = (body.email or "").strip().lower()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return generic
    if not password_reset_limiter.is_allowed(email):
        return generic

    try:
        result = (
            _db().table("users")
            .select("id, company_id, email, is_active, password_hash")
            .eq("email", email)
            .execute()
        )
    except Exception as exc:
        log.error("Forgot-password lookup failed: %s", exc)
        return generic

    user = (result.data or [None])[0]
    if not user or not user.get("is_active", True) or not user.get("password_hash"):
        return generic

    from datetime import timedelta as _td
    token = make_account_link_token(user, "password_reset", _td(minutes=PASSWORD_RESET_MINUTES))
    send_system_email(
        _db(), company_id=user["company_id"], to=user["email"],
        subject="Reset your SecretaryAI password",
        text=(
            "Someone asked to reset the password for this SecretaryAI account.\n\n"
            f"To choose a new password, open this link within {PASSWORD_RESET_MINUTES} minutes:\n"
            f"{account_link('/reset-password', token)}\n\n"
            "If you did not ask for this, you can ignore this email — your password has not changed."
        ),
        kind="password_reset",
    )
    return generic


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return validate_new_password(v)


def _set_password(user: dict, new_password: str, extra: dict | None = None) -> None:
    """Store a new password hash and end every existing session of the user."""
    update = {
        "password_hash": hash_password(new_password),
        "password_changed_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    try:
        # End old sessions FIRST: if this cannot be recorded we do not change
        # the password and leave stolen sessions alive.
        revoke_user_sessions(user["id"])
        _db().table("users").update(update).eq("id", user["id"]).execute()
    except AuthStoreUnavailable:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable. Please try again shortly.")
    except Exception as exc:
        log.error("Password update failed for user %s: %s", user["id"], exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, _=Depends(require_rate_limit(account_link_limiter))):
    """Set a new password using an emailed reset link. Signs out all sessions."""
    user = _consume_account_link(body.token, "password_reset")
    # Receiving the reset email also proves the address is theirs.
    extra = {} if user.get("email_verified_at") else {"email_verified_at": datetime.now(timezone.utc).isoformat()}
    _set_password(user, body.new_password, extra)
    _audit(user["company_id"], "password_reset", user["id"])
    return {"status": "ok", "message": "Your password has been changed. Please sign in."}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return validate_new_password(v)


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Change the signed-in user's password. Other devices are signed out."""
    if not login_email_limiter.is_allowed(f"chpw:{user['sub']}"):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    try:
        result = (
            _db().table("users").select("id, company_id, role, password_hash, is_active")
            .eq("id", user["sub"]).execute()
        )
    except Exception as exc:
        log.error("Change-password lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    row = (result.data or [None])[0]
    if not row or not row.get("is_active", True):
        raise HTTPException(status_code=401, detail="Account not found")
    if not verify_password(body.current_password, row.get("password_hash") or ""):
        raise HTTPException(status_code=400, detail="Your current password is not correct.")

    _set_password(row, body.new_password)
    _audit(row["company_id"], "password_changed", row["id"])
    # This device stays signed in with a fresh session; every other one is out.
    import time as _time
    _time.sleep(0.002)  # the new token must be issued strictly after the cut-off
    return {"status": "ok", **_login_response(row)}


class AcceptInviteRequest(BaseModel):
    token: str
    password: str
    full_name: str = ""

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return validate_new_password(v)


@router.post("/accept-invite")
async def accept_invite(body: AcceptInviteRequest, _=Depends(require_rate_limit(account_link_limiter))):
    """An invited team member chooses a password and is signed in."""
    user = _consume_account_link(body.token, "invite")
    if user.get("password_hash"):
        raise HTTPException(status_code=400, detail="This invitation was already used. Please sign in.")
    extra: dict = {"email_verified_at": datetime.now(timezone.utc).isoformat()}
    if body.full_name.strip():
        extra["name"] = body.full_name.strip()[:120]
    _set_password(user, body.password, extra)
    _audit(user["company_id"], "invite_accepted", user["id"], role=user["role"])
    import time as _time
    _time.sleep(0.002)
    return _login_response(user)


class TokenRequest(BaseModel):
    token: str


@router.post("/verify-email")
async def verify_email(body: TokenRequest, _=Depends(require_rate_limit(account_link_limiter))):
    """Confirm an email address from the link sent at sign-up."""
    user = _consume_account_link(body.token, "email_verify")
    if not user.get("email_verified_at"):
        try:
            _db().table("users").update(
                {"email_verified_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", user["id"]).execute()
        except Exception as exc:
            log.error("Email verification update failed: %s", exc)
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")
        _audit(user["company_id"], "email_verified", user["id"])
    return {"status": "ok", "message": "Your email address is confirmed."}


def send_verification_email(user: dict) -> bool:
    from datetime import timedelta as _td
    from app.utils.mailer import send_system_email
    token = make_account_link_token(user, "email_verify", _td(days=EMAIL_VERIFY_DAYS))
    return send_system_email(
        _db(), company_id=user["company_id"], to=user["email"],
        subject="Confirm your email for SecretaryAI",
        text=(
            "Welcome to SecretaryAI.\n\n"
            "Please confirm this is your email address by opening this link:\n"
            f"{account_link('/verify-email', token)}\n\n"
            "If you did not create an account, you can ignore this email."
        ),
        kind="email_verification",
    )


@router.post("/resend-verification")
async def resend_verification(body: ForgotPasswordRequest, _=Depends(require_rate_limit(account_link_limiter))):
    """Send the confirmation email again (same answer whether or not it exists)."""
    generic = {"status": "ok", "message": "If that account needs confirming, a new link is on its way."}
    email = (body.email or "").strip().lower()
    if not password_reset_limiter.is_allowed(f"verify:{email}"):
        return generic
    try:
        result = (
            _db().table("users").select("id, company_id, email, is_active, password_hash, email_verified_at")
            .eq("email", email).execute()
        )
        user = (result.data or [None])[0]
        if user and user.get("is_active", True) and not user.get("email_verified_at"):
            send_verification_email(user)
    except Exception as exc:
        log.error("Resend-verification failed: %s", exc)
    return generic


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_reason(value: str) -> str:
    """Reduce a provider-supplied error code to a short, URL-safe token."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", value or "")[:40]
    return cleaned or "error"


def _app_base_url() -> str:
    if settings.frontend_url:
        return settings.frontend_url.rstrip("/")
    if settings.debug:
        return "http://localhost:5173"
    return "https://app.secretaryai.com"
