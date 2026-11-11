"""Staging-only password recovery and password changes for existing accounts.

Uses the current `users.hashed_password` field, not the older Pass 4 database
layout. Reset links are short-lived, single-use opaque tokens held only as
SHA-256 digests in Redis. No provider call is made outside the configured
staging mailbox allowlist.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from uuid import UUID
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.auth.jwt import create_access_token, hash_password, password_session_version, verify_password
from app.auth.rbac import get_current_user
from app.config import get_settings
from app.utils.qbo_token_store import create_qbo_client
from app.utils.rate_limiter import RedisRateLimiter, require_rate_limit


router = APIRouter()
log = logging.getLogger(__name__)
_RESET_SECONDS = 30 * 60
_RESET_PREFIX = "lionbi_password_reset:"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IP_LIMITER = RedisRateLimiter(max_calls=10, window_seconds=60, prefix="rl:account-recovery")
_EMAIL_LIMITER = RedisRateLimiter(max_calls=3, window_seconds=60 * 60, prefix="rl:password-reset-email")
_CHANGE_LIMITER = RedisRateLimiter(max_calls=5, window_seconds=15 * 60, prefix="rl:change-password")


def _settings():
    settings = get_settings()
    if settings.secretaryai_deployment_env.strip().lower() != "staging":
        raise HTTPException(status_code=404, detail="Not found")
    return settings


def _db():
    return create_qbo_client(get_settings())


def _redis():
    import redis
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


def _reset_key(token: str) -> str:
    return f"{_RESET_PREFIX}{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def _reset_url(token: str, settings) -> str:
    address = urlsplit(settings.frontend_url)
    if (address.scheme != "https" or address.hostname != "secretaryai-web-staging.up.railway.app"
            or address.username or address.password or address.port is not None
            or address.query or address.fragment):
        raise HTTPException(status_code=503, detail="Recovery URL not configured safely")
    # The fragment stays in the browser and is not sent in the HTTP request.
    return f"https://{address.hostname}/reset-password#token={token}"


def _send_reset_mail(email: str, url: str, settings) -> None:
    if (not settings.secretaryai_invite_delivery_enabled
            or email != settings.secretaryai_invite_email_recipient.strip().lower()):
        raise HTTPException(status_code=403, detail="Password recovery email disabled")
    message = (
        "Someone requested a password reset for your Lion BI staging account.\n\n"
        "Open this link within 30 minutes to choose a new password:\n"
        f"{url}\n\nIf you didn't request this, you can ignore this email.\n\nLion BI"
    )
    subject = "Lion BI staging — Reset your password"
    resend_key = settings.resend_api_key or settings.resed_api_key
    if resend_key:
        import httpx
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            json={"from": settings.from_email, "to": [email], "subject": subject, "text": message},
            timeout=10.0,
        )
        if response.status_code not in (200, 201, 202):
            raise RuntimeError("Mail provider rejected password reset")
        return
    if settings.sendgrid_api_key:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        response = SendGridAPIClient(api_key=settings.sendgrid_api_key).send(
            Mail(from_email=settings.from_email, to_emails=email, subject=subject,
                 plain_text_content=message)
        )
        if response.status_code not in (200, 201, 202):
            raise RuntimeError("Mail provider rejected password reset")
        return
    raise RuntimeError("No staging identity email provider configured")


def _require_audit(db, *, company_id: str, actor_id: str, event_type: str) -> None:
    from app.utils.audit import write_audit_event
    try:
        audit_id = write_audit_event(
            db, company_id=company_id, event_type=event_type,
            actor_id=actor_id, action_class="commit", capability="account_security",
            path_used="api", metadata={"gate": "staging_password_security"},
        )
    except Exception:
        audit_id = None
    if not audit_id:
        raise HTTPException(status_code=503, detail="Account security audit unavailable")


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)


def _valid_password(value: str) -> str:
    if (len(value) < 8 or len(value.encode("utf-8")) > 72 or "\x00" in value
            or not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value)):
        raise ValueError("Password needs 8+ characters including a letter and number (72-byte limit)")
    return value


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=200)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _valid_password(value)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _valid_password(value)


@router.post("/forgot-password", dependencies=[Depends(require_rate_limit(_IP_LIMITER))])
async def forgot_password(body: ForgotPasswordRequest):
    settings = _settings()
    generic = {"status": "ok", "message": "If that email has an account, a reset link is on its way."}
    email = body.email.strip().lower()
    # Staging: never email an unapproved test mailbox or reveal whether it exists.
    if (not _EMAIL_RE.fullmatch(email) or not settings.secretaryai_invite_delivery_enabled
            or email != settings.secretaryai_invite_email_recipient.strip().lower()):
        return generic
    if not _EMAIL_LIMITER.is_allowed(email):
        return generic
    db = _db()
    try:
        rows = db.table("users").select("id,company_id,hashed_password,is_active").eq(
            "email", email
        ).limit(2).execute().data
    except Exception:
        log.warning("Password recovery lookup unavailable")
        return generic
    if len(rows or []) != 1 or rows[0].get("is_active") is not True or not rows[0].get("hashed_password"):
        return generic
    row = rows[0]
    try:
        _require_audit(db, company_id=row["company_id"], actor_id=row["id"],
                       event_type="password_reset_link_requested")
        token = secrets.token_urlsafe(32)
        key = _reset_key(token)
        value = json.dumps({"user_id": row["id"], "company_id": row["company_id"],
                            "password_fingerprint": _fingerprint(row["hashed_password"])})
        if not _redis().setex(key, _RESET_SECONDS, value):
            raise RuntimeError("Recovery token not saved")
        try:
            await run_in_threadpool(_send_reset_mail, email, _reset_url(token, settings), settings)
        except Exception:
            try:
                _redis().delete(key)
            except Exception:
                log.warning("Could not revoke undelivered password reset link")
            raise
    except Exception:
        log.warning("Password recovery request could not be delivered")
    return generic


@router.post("/reset-password", dependencies=[Depends(require_rate_limit(_IP_LIMITER))])
async def reset_password(body: ResetPasswordRequest):
    _settings()
    bad = HTTPException(status_code=400, detail="This reset link is invalid or expired")
    try:
        raw = _redis().getdel(_reset_key(body.token))
    except Exception:
        raise HTTPException(status_code=503, detail="Password recovery temporarily unavailable") from None
    if not raw:
        raise bad
    try:
        link = json.loads(raw)
        user_id, company_id = str(UUID(link["user_id"])), str(UUID(link["company_id"]))
        fingerprint = link["password_fingerprint"]
        if not isinstance(fingerprint, str):
            raise ValueError("Invalid password marker")
    except (ValueError, TypeError, KeyError):
        raise bad from None
    db = _db()
    try:
        rows = db.table("users").select("id,company_id,hashed_password,is_active").eq(
            "id", user_id
        ).eq("company_id", company_id).limit(2).execute().data
    except Exception:
        raise HTTPException(status_code=503, detail="Password recovery temporarily unavailable") from None
    if (len(rows or []) != 1 or rows[0].get("is_active") is not True
            or not isinstance(rows[0].get("hashed_password"), str)
            or not hmac.compare_digest(_fingerprint(rows[0]["hashed_password"]), fingerprint)):
        raise bad
    old_hash = rows[0]["hashed_password"]
    new_hash = await run_in_threadpool(hash_password, body.new_password)
    _require_audit(db, company_id=company_id, actor_id=user_id,
                   event_type="password_reset_change_requested")
    try:
        updated = db.table("users").update({"hashed_password": new_hash}).eq(
            "id", user_id
        ).eq("company_id", company_id).eq("hashed_password", old_hash).eq(
            "is_active", True
        ).execute().data
    except Exception:
        raise HTTPException(status_code=503, detail="Password recovery temporarily unavailable") from None
    if len(updated or []) != 1:
        raise bad
    # Changing the password hash makes every prior access and refresh JWT's
    # signed `pwd` claim stale, across workers/devices, without a DB migration.
    try:
        _require_audit(db, company_id=company_id, actor_id=user_id,
                       event_type="password_reset_completed")
    except HTTPException:
        log.warning("Password reset completion event unavailable after write")
    return {"status": "ok", "message": "Password changed. Please sign in."}


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, actor: dict = Depends(get_current_user)):
    _settings()
    if not _CHANGE_LIMITER.is_allowed(actor["sub"]):
        raise HTTPException(status_code=429, detail="Too many password changes attempted")
    db = _db()
    try:
        rows = db.table("users").select("id,company_id,role,hashed_password,is_active").eq(
            "id", actor["sub"]
        ).eq("company_id", actor["company_id"]).limit(2).execute().data
    except Exception:
        raise HTTPException(status_code=503, detail="Account temporarily unavailable") from None
    if len(rows or []) != 1 or rows[0].get("is_active") is not True:
        raise HTTPException(status_code=401, detail="Account unavailable")
    user = rows[0]
    if user["role"] != actor["role"] or not verify_password(body.current_password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect or session changed")
    new_hash = await run_in_threadpool(hash_password, body.new_password)
    _require_audit(db, company_id=actor["company_id"], actor_id=actor["sub"],
                   event_type="password_change_requested")
    try:
        updated = db.table("users").update({"hashed_password": new_hash}).eq(
            "id", actor["sub"]
        ).eq("company_id", actor["company_id"]).eq("role", user["role"]).eq(
            "hashed_password", user["hashed_password"]
        ).eq("is_active", True).execute().data
    except Exception:
        raise HTTPException(status_code=503, detail="Password change temporarily unavailable") from None
    if len(updated or []) != 1:
        raise HTTPException(status_code=409, detail="Account changed; please sign in again")
    try:
        _require_audit(db, company_id=actor["company_id"], actor_id=actor["sub"],
                       event_type="password_changed")
    except HTTPException:
        log.warning("Password-change completion event unavailable after write")
    return {"status": "ok", "access_token": create_access_token({
        "sub": user["id"], "company_id": user["company_id"], "role": user["role"],
        "pwd": password_session_version(user["id"], new_hash),
    }), "token_type": "bearer"}
