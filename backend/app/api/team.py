"""Staging-only, mailbox-verified manager invitations.

Unlike the public company-registration route, this flow never creates another
company and never gives the inviting owner access to the recipient's token or
password. General-purpose outbound email remains disabled in staging.
"""

import hashlib
import json
import logging
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.auth.jwt import hash_password
from app.auth.rbac import get_current_user
from app.config import get_settings
from app.utils.qbo_token_store import create_qbo_client
from app.utils.rate_limiter import login_limiter, require_rate_limit


router = APIRouter()
log = logging.getLogger(__name__)
_TTL_SECONDS = 48 * 60 * 60
_REDIS_PREFIX = "secretaryai_manager_invite:"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INVITATION_SUBJECT = "Lion BI staging — Manager invitation"


def _staging_settings():
    settings = get_settings()
    if settings.secretaryai_deployment_env.strip().lower() != "staging":
        raise HTTPException(status_code=404, detail="Not found")
    return settings


def _db():
    return create_qbo_client(get_settings())


def _redis():
    import redis
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def _owner_record(db, actor: dict) -> dict:
    """Enforce current database role; JWT roles alone can become stale."""
    if actor.get("role") != "owner" or not actor.get("sub") or not actor.get("company_id"):
        raise HTTPException(status_code=403, detail="Only an active company owner can invite managers")
    try:
        rows = (
            db.table("users").select("id,company_id,role,is_active")
            .eq("id", actor["sub"]).eq("company_id", actor["company_id"])
            .limit(2).execute().data
        )
    except Exception:
        raise HTTPException(status_code=503, detail="User authorization unavailable") from None
    if len(rows or []) != 1 or rows[0].get("role") != "owner" or rows[0].get("is_active") is not True:
        raise HTTPException(status_code=403, detail="Only an active company owner can invite managers")
    return rows[0]


class InviteManagerRequest(BaseModel):
    email: str = Field(max_length=254)
    full_name: str = Field(min_length=2, max_length=120)
    role: Literal["manager"] = "manager"

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL.fullmatch(value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Please provide the full name")
        return value


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    password: str

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        if (len(value) < 8 or len(value.encode("utf-8")) > 72 or "\x00" in value
                or not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value)):
            raise ValueError("Password needs 8+ characters, including a letter and number (72-byte maximum)")
        return value


def _invitation_url(raw_token: str) -> str:
    settings = _staging_settings()
    url = urlsplit(settings.frontend_url)
    if (url.scheme != "https" or url.hostname != "secretaryai-web-staging.up.railway.app"
            or url.username or url.password or url.query or url.fragment):
        raise HTTPException(status_code=503, detail="Invitation URL not configured safely")
    # Fragment is not sent to the web server in HTTP requests or referrer headers.
    return f"https://{url.hostname}/accept-invite#token={raw_token}"


def _send_invite_email(email: str, full_name: str, link: str, settings) -> None:
    """One explicitly gated identity email; never use ordinary staging email flows."""
    if not settings.secretaryai_invite_delivery_enabled:
        raise HTTPException(status_code=503, detail="Invitation email delivery is not enabled")
    allowed = settings.secretaryai_invite_email_recipient.strip().lower()
    if not allowed or email != allowed:
        raise HTTPException(status_code=403, detail="Invitation recipient is not authorized in staging")

    body = (
        f"Hello {full_name},\n\n"
        "You have been invited to join Lion Business Intelligence (Lion BI) "
        "staging as a Manager. "
        "To accept, open this link and choose your own password:\n\n"
        f"{link}\n\n"
        "This invitation expires in 48 hours and can be used only once. "
        "If you did not expect it, you can ignore this message.\n\n"
        "Lola\nYour AI business assistant at Lion BI\n"
    )

    # This account already has a Resend API key in its staging environment.
    # Accept the correctly spelled key as well as the legacy RESED_API_KEY name.
    resend_key = settings.resend_api_key or settings.resed_api_key
    if resend_key:
        import httpx
        try:
            response = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={
                    "from": settings.from_email,
                    "to": [email],
                    "subject": _INVITATION_SUBJECT,
                    "text": body,
                },
                timeout=10.0,
            )
            if response.status_code not in (200, 201, 202):
                raise RuntimeError("Email provider rejected invitation")
        except Exception:
            log.warning("Staging invitation provider did not accept the email")
            raise HTTPException(status_code=503, detail="Could not send invitation; check email provider") from None
        return

    if settings.sendgrid_api_key:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        try:
            resp = SendGridAPIClient(api_key=settings.sendgrid_api_key).send(
                Mail(from_email=settings.from_email, to_emails=email,
                     subject=_INVITATION_SUBJECT, plain_text_content=body)
            )
            if resp.status_code not in (200, 201, 202):
                raise RuntimeError("Email provider rejected invitation")
        except Exception:
            log.warning("Staging invitation provider did not accept the email")
            raise HTTPException(status_code=503, detail="Could not send invitation; check email provider") from None
        return
    raise HTTPException(status_code=503, detail="No invitation email provider configured")


def _audit(db, company_id: str, event: str, actor: str | None, metadata: dict) -> None:
    try:
        from app.utils.audit import write_audit_event
        write_audit_event(db, company_id=company_id, event_type=event,
                          actor_id=actor, metadata=metadata)
    except Exception:
        log.warning("Invitation audit event unavailable")


@router.post("/invitations", status_code=201)
async def invite_manager(
    body: InviteManagerRequest,
    actor: dict = Depends(get_current_user),
    _=Depends(require_rate_limit(login_limiter)),
):
    settings = _staging_settings()
    db = _db()
    _owner_record(db, actor)
    if not settings.secretaryai_invite_delivery_enabled:
        raise HTTPException(status_code=503, detail="Invitation email delivery is not enabled")
    if body.email != settings.secretaryai_invite_email_recipient.strip().lower():
        raise HTTPException(status_code=403, detail="Invitation recipient is not authorized in staging")
    try:
        existing = db.table("users").select("id").eq("email", body.email).limit(1).execute().data
    except Exception:
        raise HTTPException(status_code=503, detail="Account lookup unavailable") from None
    if existing:
        raise HTTPException(status_code=409, detail="This email already has an account")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    key = f"{_REDIS_PREFIX}{token_hash}"
    data = {
        "company_id": actor["company_id"], "email": body.email,
        "full_name": body.full_name, "role": "manager", "invited_by": actor["sub"],
    }
    try:
        if not _redis().setex(key, _TTL_SECONDS, json.dumps(data)):
            raise RuntimeError("Redis refused token write")
    except Exception:
        raise HTTPException(status_code=503, detail="Invitation token store unavailable") from None

    try:
        await run_in_threadpool(_send_invite_email, body.email, body.full_name,
                                _invitation_url(token), settings)
    except Exception:
        try:
            _redis().delete(key)
        except Exception:
            log.warning("Could not revoke failed invitation token")
        raise

    _audit(db, actor["company_id"], "manager_invited", actor["sub"], {"role": "manager"})
    return {"status": "sent", "role": "manager", "expires_in_hours": 48}


@router.post("/invitations/accept", status_code=201)
async def accept_invitation(body: AcceptInviteRequest, _=Depends(require_rate_limit(login_limiter))):
    _staging_settings()
    try:
        password_hash = await run_in_threadpool(hash_password, body.password)
    except Exception:
        raise HTTPException(status_code=503, detail="Password processing unavailable") from None
    key = f"{_REDIS_PREFIX}{hashlib.sha256(body.token.encode('utf-8')).hexdigest()}"
    try:
        raw = _redis().getdel(key)
    except Exception:
        raise HTTPException(status_code=503, detail="Invitation token store unavailable") from None
    if not raw:
        raise HTTPException(status_code=400, detail="Invitation invalid, expired, or already used")

    try:
        invitation = json.loads(raw)
        if (invitation.get("role") != "manager" or not _EMAIL.fullmatch(invitation["email"])
                or not invitation.get("company_id") or not invitation.get("invited_by")):
            raise ValueError("Malformed invitation")
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid invitation") from None

    db = _db()
    # An owner who has since lost their permissions cannot onboard an approver.
    _owner_record(db, {"sub": invitation["invited_by"],
                       "company_id": invitation["company_id"], "role": "owner"})
    try:
        created = (
            db.table("users").insert({
                "company_id": invitation["company_id"],
                "email": invitation["email"],
                "full_name": invitation["full_name"],
                "role": "manager", "is_active": True,
                "hashed_password": password_hash,
            }).execute().data
        )
    except Exception:
        log.warning("Manager invitation acceptance failed to create account")
        raise HTTPException(status_code=409, detail="Could not activate invitation; ask owner to resend") from None
    if not created:
        raise HTTPException(status_code=503, detail="Account activation unavailable")
    _audit(db, invitation["company_id"], "manager_invitation_accepted",
           created[0]["id"], {"role": "manager", "invited_by": invitation["invited_by"]})
    return {"status": "accepted", "role": "manager", "message": "You can now sign in"}


@router.get("/members")
async def list_team_members(actor: dict = Depends(get_current_user)):
    _staging_settings()
    db = _db()
    _owner_record(db, actor)
    try:
        rows = (
            db.table("users").select("id,full_name,email,role,is_active")
            .eq("company_id", actor["company_id"]).order("created_at").limit(100).execute().data
        )
    except Exception:
        raise HTTPException(status_code=503, detail="Team members unavailable") from None
    return {"members": rows or []}
