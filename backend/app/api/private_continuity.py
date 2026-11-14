"""Employee-private Lola continuity APIs.

Staging-only foundation. No third-party messages are read or sent here.
Provider sync workers must verify source ownership before recording an event.
Every service-role query is scoped to BOTH authenticated company and user.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.intelligence.private_continuity import pending_followups

router = APIRouter()


def _scope(user: dict) -> tuple[str, str]:
    company_id = str(user.get("company_id") or "")
    user_id = str(user.get("sub") or "")
    if not company_id or not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return company_id, user_id


def _owned(db, table: str, user: dict):
    company_id, user_id = _scope(user)
    return db.table(table).select("*").eq("company_id", company_id).eq("user_id", user_id)


def _contact(db, user: dict, contact_id: str, *, verified: bool = False) -> dict:
    try:
        row = _owned(db, "lola_private_contacts", user).eq("id", str(UUID(contact_id))).limit(1).execute()
    except ValueError:
        raise HTTPException(status_code=404, detail="Contact not found") from None
    except Exception:
        raise HTTPException(status_code=503, detail="Private intelligence storage unavailable") from None
    contact = (row.data or [None])[0]
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if verified and not contact.get("identity_verified"):
        raise HTTPException(status_code=409, detail="Confirm contact identity before linking communications")
    return contact


class NewContact(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)


class NewEvent(BaseModel):
    contact_id: str
    project_key: str = Field(min_length=1, max_length=140, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9 _./-]*$")
    channel: str = Field(pattern=r"^(email|whatsapp|note|document)$")
    direction: str = Field(pattern=r"^(inbound|outbound)$")
    kind: str = Field(pattern=r"^(request|ack|draft_sent|quote_sent|followup_sent|feedback|closed|other)$")
    occurred_at: datetime
    summary: str = Field(default="", max_length=1000)
    source_ref: str | None = Field(default=None, max_length=256)


class SnoozeRequest(BaseModel):
    until: datetime


@router.get("/contacts")
def list_private_contacts(user: dict = Depends(get_current_user), db=Depends(get_db)):
    try:
        rows = _owned(db, "lola_private_contacts", user).order("display_name").limit(200).execute()
        return {"contacts": rows.data or []}
    except Exception:
        raise HTTPException(status_code=503, detail="Private contacts unavailable") from None


@router.post("/contacts", status_code=201)
def create_private_contact(body: NewContact, user: dict = Depends(get_current_user), db=Depends(get_db)):
    company_id, user_id = _scope(user)
    try:
        res = db.table("lola_private_contacts").insert({
            "company_id": company_id, "user_id": user_id,
            "display_name": body.display_name.strip(),
            "email": body.email.strip().lower() if body.email else None,
            "phone": body.phone.strip() if body.phone else None,
            "identity_verified": False,
        }).execute()
        return {"contact": (res.data or [None])[0], "requires_identity_confirmation": True}
    except Exception:
        raise HTTPException(status_code=503, detail="Could not create private contact") from None


@router.post("/contacts/{contact_id}/confirm")
def confirm_contact_identity(contact_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    contact = _contact(db, user, contact_id)
    if not (contact.get("email") or contact.get("phone")):
        raise HTTPException(status_code=409, detail="Add at least one contact identifier first")
    company_id, user_id = _scope(user)
    try:
        db.table("lola_private_contacts").update({"identity_verified": True}).eq(
            "company_id", company_id
        ).eq("user_id", user_id).eq("id", contact["id"]).execute()
        return {"contact_id": contact["id"], "identity_verified": True}
    except Exception:
        raise HTTPException(status_code=503, detail="Contact verification unavailable") from None


@router.post("/events", status_code=201)
def record_private_event(body: NewEvent, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Record a private user-confirmed activity; NEVER dispatch a provider send."""
    company_id, user_id = _scope(user)
    contact = _contact(db, user, body.contact_id, verified=True)
    occurred = body.occurred_at
    if occurred.tzinfo is None or occurred > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Valid past timezone-aware timestamp required")
    if body.source_ref:
        try:
            existing = (_owned(db, "lola_private_events", user)
                .eq("channel", body.channel).eq("source_ref", body.source_ref)
                .limit(1).execute())
            if existing.data:
                return {"event": existing.data[0], "deduplicated": True}
        except Exception:
            raise HTTPException(status_code=503, detail="Private event verification unavailable") from None
    try:
        rows = db.table("lola_private_events").insert({
            "company_id": company_id, "user_id": user_id,
            "contact_id": contact["id"], "project_key": body.project_key.strip().lower(),
            "channel": body.channel, "direction": body.direction, "kind": body.kind,
            "occurred_at": occurred.isoformat(), "summary": body.summary,
            "source_ref": body.source_ref,
        }).execute()
        return {"event": (rows.data or [None])[0], "deduplicated": False}
    except Exception:
        raise HTTPException(status_code=503, detail="Could not record private event") from None


@router.get("/timeline/{contact_id}")
def private_timeline(
    contact_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
    limit: int = Query(default=100, ge=1, le=200),
):
    contact = _contact(db, user, contact_id)
    try:
        events = (_owned(db, "lola_private_events", user)
                  .eq("contact_id", contact["id"])
                  .order("occurred_at", desc=True).limit(limit).execute())
        return {"contact": contact, "events": events.data or []}
    except Exception:
        raise HTTPException(status_code=503, detail="Private timeline unavailable") from None


@router.get("/followups")
def private_followups(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """On-demand due list; durable scheduler/push must be added before GA."""
    company_id, user_id = _scope(user)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        events = (_owned(db, "lola_private_events", user)
                  .gte("occurred_at", cutoff)
                  .order("occurred_at", desc=True).limit(2000).execute()).data or []
        contacts = (_owned(db, "lola_private_contacts", user).limit(500).execute()).data or []
        state = (_owned(db, "lola_private_followup_state", user).limit(2000).execute()).data or []
    except Exception:
        raise HTTPException(status_code=503, detail="Private follow-ups unavailable") from None
    by_contact = {str(c["id"]): c for c in contacts}
    by_event = {str(s["event_id"]): s for s in state}
    now = datetime.now(timezone.utc)
    items = []
    for item in pending_followups(events, now=now):
        progress = by_event.get(str(item["event_id"]), {})
        if progress.get("resolved_at"):
            continue
        snoozed = progress.get("snoozed_until")
        if snoozed and datetime.fromisoformat(snoozed.replace("Z", "+00:00")) > now:
            continue
        contact = by_contact.get(str(item["contact_id"]))
        if not contact:
            continue
        item["contact_name"] = contact["display_name"]
        item["available_channels"] = [
            channel for channel, key in (("email", "email"), ("whatsapp", "phone"))
            if contact.get(key)
        ]
        item["draft_suggestion"] = (
            f"Hi {contact['display_name']}, following up on {item['project_key']}. "
            "Have you had a chance to review the draft?"
        )
        items.append(item)
    return {"followups": items, "count": len(items), "private": True}


@router.post("/followups/{event_id}/snooze")
def snooze_private_followup(
    event_id: str, body: SnoozeRequest,
    user: dict = Depends(get_current_user), db=Depends(get_db),
):
    _require_owned_event(db, user, event_id)
    now = datetime.now(timezone.utc)
    if body.until.tzinfo is None or not (now < body.until <= now + timedelta(days=60)):
        raise HTTPException(status_code=422, detail="Snooze must be within 60 days")
    return _set_state(db, user, event_id, {"snoozed_until": body.until.isoformat()})


@router.post("/followups/{event_id}/resolve")
def resolve_private_followup(event_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    _require_owned_event(db, user, event_id)
    return _set_state(db, user, event_id, {"resolved_at": datetime.now(timezone.utc).isoformat()})


def _require_owned_event(db, user: dict, event_id: str):
    try:
        found = _owned(db, "lola_private_events", user).eq("id", str(UUID(event_id))).limit(1).execute()
        if not found.data:
            raise HTTPException(status_code=404, detail="Follow-up not found")
    except ValueError:
        raise HTTPException(status_code=404, detail="Follow-up not found") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Private follow-up unavailable") from None


def _set_state(db, user: dict, event_id: str, fields: dict):
    company_id, user_id = _scope(user)
    try:
        db.table("lola_private_followup_state").upsert({
            "company_id": company_id, "user_id": user_id, "event_id": str(UUID(event_id)),
            "updated_at": datetime.now(timezone.utc).isoformat(), **fields,
        }, on_conflict="company_id,user_id,event_id").execute()
        return {"event_id": event_id, "updated": True}
    except Exception:
        raise HTTPException(status_code=503, detail="Could not update private follow-up") from None
