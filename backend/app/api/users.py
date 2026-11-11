"""
Team management — the owner adds, changes and removes staff accounts.

GET    /api/users                      list everyone in the company
POST   /api/users/invite               invite a person by email with a role
POST   /api/users/{id}/resend-invite   send a fresh invitation link
PATCH  /api/users/{id}                 change role / name, deactivate or reactivate

Rules enforced here (not just in the UI):
  * owner only (`manage_users`);
  * every lookup and update is scoped to the caller's company;
  * you cannot change your own role or deactivate yourself;
  * a company always keeps at least one active owner;
  * changing a role or deactivating someone ends their sessions immediately;
  * every change is written to the audit trail.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.api.deps import get_db
from app.auth.jwt import AuthStoreUnavailable, revoke_user_sessions
from app.auth.rbac import ROLE_PERMISSIONS, require_permission
from app.utils.audit import write_audit_event
from app.utils.mailer import email_configured, send_system_email
from app.utils.rate_limiter import password_reset_limiter

log = logging.getLogger(__name__)
router = APIRouter()

# Roles an owner can hand out. A second owner is allowed (so the business is
# not locked out if one person leaves), but only by an existing owner.
ASSIGNABLE_ROLES = tuple(ROLE_PERMISSIONS.keys())
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PUBLIC_FIELDS = "id, name, email, role, is_active, totp_enabled, invited_at, email_verified_at, last_login_at, created_at, password_hash"


def _public(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "email": row.get("email"),
        "role": row.get("role"),
        "is_active": bool(row.get("is_active", True)),
        "two_factor": bool(row.get("totp_enabled")),
        # invited but has not chosen a password yet
        "pending_invite": not row.get("password_hash"),
        "invited_at": row.get("invited_at"),
        "last_login_at": row.get("last_login_at"),
        "created_at": row.get("created_at"),
    }


def _get_user(db, user_id: str, company_id: str) -> dict:
    try:
        result = (
            db.table("users").select(_PUBLIC_FIELDS)
            .eq("id", user_id).eq("company_id", company_id).execute()
        )
    except Exception as exc:
        log.error("User lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if not result.data:
        # Someone else's user looks exactly like a user that does not exist.
        raise HTTPException(status_code=404, detail="User not found")
    return result.data[0]


def _active_owner_count(db, company_id: str) -> int:
    result = (
        db.table("users").select("id")
        .eq("company_id", company_id).eq("role", "owner").eq("is_active", True).execute()
    )
    return len(result.data or [])


def _send_invite(db, target: dict, company_id: str, inviter_id: str) -> dict:
    """Email the invitation; if email is not set up, hand the link to the owner."""
    from app.api.auth import INVITE_DAYS, account_link, make_account_link_token

    token = make_account_link_token(
        {"id": target["id"], "password_hash": None}, "invite", timedelta(days=INVITE_DAYS)
    )
    link = account_link("/accept-invite", token)
    sent = False
    if email_configured():
        sent = send_system_email(
            db, company_id=company_id, to=target["email"],
            subject="You've been invited to SecretaryAI",
            text=(
                "You have been invited to join your company's SecretaryAI account.\n\n"
                f"Choose your password here (the link works for {INVITE_DAYS} days):\n{link}\n\n"
                "If you were not expecting this, you can ignore this email."
            ),
            kind="user_invite", actor=inviter_id,
        )
    out = {"email_sent": sent}
    if not sent:
        # The owner is authorised to create this account, so they may carry
        # the link to the person themselves.
        out["invite_link"] = link
        out["note"] = "The invitation email could not be sent. Give this link to the person directly."
    return out


@router.get("")
@router.get("/", include_in_schema=False)
async def list_users(user: dict = Depends(require_permission("manage_users")), db=Depends(get_db)):
    try:
        result = (
            db.table("users").select(_PUBLIC_FIELDS)
            .eq("company_id", user["company_id"]).order("created_at", desc=False).execute()
        )
    except Exception as exc:
        log.error("User list failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"users": [_public(r) for r in (result.data or [])], "roles": list(ASSIGNABLE_ROLES)}


class InviteRequest(BaseModel):
    email: str
    role: str
    name: str = ""

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ASSIGNABLE_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ASSIGNABLE_ROLES)}")
        return v


@router.post("/invite", status_code=201)
async def invite_user(
    body: InviteRequest,
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    try:
        existing = db.table("users").select("id").eq("email", body.email).execute()
    except Exception as exc:
        log.error("Invite email check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if existing.data:
        raise HTTPException(status_code=409, detail="That email address already has an account.")

    row = {
        "company_id": company_id,          # always the inviter's company
        "email": body.email,
        "role": body.role,
        "is_active": True,
        "password_hash": None,             # set when the invite is accepted
        "invited_by": user["sub"],
        "invited_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.name.strip():
        row["name"] = body.name.strip()[:120]
    try:
        created = db.table("users").insert(row).execute()
    except Exception as exc:
        log.error("Invite insert failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if not created.data:
        raise HTTPException(status_code=500, detail="Could not create the user")
    target = created.data[0]

    write_audit_event(
        db, company_id=company_id, event_type="user_invited", actor_id=user["sub"],
        metadata={"user_id": target["id"], "email": body.email, "role": body.role},
    )
    return {"user": _public(target), **_send_invite(db, target, company_id, user["sub"])}


@router.post("/{user_id}/resend-invite")
async def resend_invite(
    user_id: str,
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    target = _get_user(db, user_id, user["company_id"])
    if target.get("password_hash"):
        raise HTTPException(status_code=400, detail="This person has already accepted their invitation.")
    if not target.get("is_active", True):
        raise HTTPException(status_code=400, detail="This account is deactivated.")
    if not password_reset_limiter.is_allowed(f"invite:{target['id']}"):
        raise HTTPException(status_code=429, detail="Too many invitations sent. Try again later.")
    return _send_invite(db, target, user["company_id"], user["sub"])


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _role(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ASSIGNABLE_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ASSIGNABLE_ROLES)}")
        return v


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    target = _get_user(db, user_id, company_id)
    is_self = str(target["id"]) == str(user["sub"])

    update: dict = {}
    if body.name is not None and body.name.strip():
        update["name"] = body.name.strip()[:120]

    role_changes = body.role is not None and body.role != target.get("role")
    deactivating = body.is_active is False and target.get("is_active", True)
    reactivating = body.is_active is True and not target.get("is_active", True)

    if is_self and (role_changes or deactivating):
        raise HTTPException(status_code=400, detail="You cannot change your own role or deactivate yourself.")

    losing_owner = target.get("role") == "owner" and target.get("is_active", True) and (
        deactivating or (role_changes and body.role != "owner")
    )
    if losing_owner and _active_owner_count(db, company_id) <= 1:
        raise HTTPException(status_code=400, detail="A company must keep at least one active owner.")

    if role_changes:
        update["role"] = body.role
    if deactivating:
        update["is_active"] = False
        update["deactivated_at"] = datetime.now(timezone.utc).isoformat()
    if reactivating:
        update["is_active"] = True
        update["deactivated_at"] = None

    if not update:
        return {"user": _public(target)}

    # End their sessions BEFORE saving a reduction in access. If that cannot be
    # recorded we stop: otherwise the person would keep their old access until
    # their token expired.
    if role_changes or deactivating:
        try:
            revoke_user_sessions(str(target["id"]))
        except AuthStoreUnavailable:
            raise HTTPException(status_code=503, detail="Could not apply the change right now. Please try again.")

    try:
        saved = (
            db.table("users").update(update)
            .eq("id", user_id).eq("company_id", company_id).execute()
        )
    except Exception as exc:
        log.error("User update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    event = "user_deactivated" if deactivating else "user_reactivated" if reactivating else "user_updated"
    write_audit_event(
        db, company_id=company_id, event_type=event, actor_id=user["sub"],
        before_snapshot={"role": target.get("role"), "is_active": target.get("is_active", True)},
        after_snapshot={k: v for k, v in update.items() if k in ("role", "is_active")},
        metadata={"user_id": str(target["id"]), "email": target.get("email")},
    )
    row = (saved.data or [None])[0] or {**target, **update}
    return {"user": _public(row)}
