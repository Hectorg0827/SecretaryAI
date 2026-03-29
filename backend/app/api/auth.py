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
    build_authorization_url, exchange_code_for_tokens, revoke_token,
    build_google_authorization_url, exchange_google_code_for_tokens, revoke_google_token,
)
from app.auth.jwt import create_access_token, decode_access_token, verify_password, hash_password, revoke_token
from app.auth.rbac import get_current_user, oauth2_scheme
from app.config import get_settings
from app.utils.encryption import encrypt, decrypt
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

        import pyotp
        if not pyotp.TOTP(secret).verify(body.totp_code, valid_window=1):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")

        token = create_access_token({
            "sub": user["id"],
            "company_id": user["company_id"],
            "role": user["role"],
        })
        return {"access_token": token, "token_type": "bearer",
                "company_id": user["company_id"], "role": user["role"]}

    # ── First-step: email + password ──────────────────────────────────────────
    if not body.email or not body.password:
        raise HTTPException(status_code=422, detail="email and password are required")

    try:
        result = (
            db.table("users")
            .select("id, company_id, role, password_hash, is_active, totp_enabled, totp_secret")
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
        import pyotp
        if not pyotp.TOTP(secret).verify(body.totp_code, valid_window=1):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")

    token = create_access_token({
        "sub": user["id"],
        "company_id": user["company_id"],
        "role": user["role"],
    })

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


@router.post("/logout")
async def logout(
    token: str = Depends(oauth2_scheme),
    _user: dict = Depends(get_current_user),
):
    """
    Revoke the current JWT by adding its JTI to the Redis blacklist.
    The token becomes invalid immediately, even before its natural expiry.
    """
    revoke_token(token)
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
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r'[A-Za-z]', v):
            raise ValueError("Password must contain at least one letter")
        if not re.search(r'[0-9]', v):
            raise ValueError("Password must contain at least one number")
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

    if not pyotp.TOTP(secret).verify(body.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

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
    return {"enabled": False}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _app_base_url() -> str:
    if settings.frontend_url:
        return settings.frontend_url.rstrip("/")
    if settings.debug:
        return "http://localhost:5173"
    return "https://app.secretaryai.com"
