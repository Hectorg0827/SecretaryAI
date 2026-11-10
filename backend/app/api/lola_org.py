"""
Org & access administration — the company's hierarchy that Lola enforces.

GET    /api/lola/org              people + their org profile (owner only)
PUT    /api/lola/org/{user_id}    set one person's profile (owner only)
DELETE /api/lola/org/{user_id}    remove a profile → back to role defaults (owner only)

Rules (all server-side):
* Caller must be an ACTIVE OWNER in the database (the token's role is ignored).
* A profile can never lift someone above their role: only managers can be
  department managers or executives; owners are always executives; everyone
  else is an employee.
* "Reports to" must be an active member of the same company, never the person
  themselves, and may not create a reporting loop.
* Every change is written to the audit log with the before/after values.
Clearances (finance / HR) and approval limits take effect on the person's next
request — Lola re-reads them every time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.lola.authorization import AuthorizationDenied, AuthorizationUnavailable, build_principal
from app.lola.contracts import Department, OrgLevel
from app.utils.audit import write_audit_event

log = logging.getLogger(__name__)
router = APIRouter()

MAX_TERRITORIES = 50
MAX_CHAIN = 50
_ALLOWED_LEVELS = {
    "owner": {OrgLevel.EXECUTIVE},
    "manager": {OrgLevel.EXECUTIVE, OrgLevel.DEPARTMENT_MANAGER, OrgLevel.EMPLOYEE},
}
_PROFILE_FIELDS = ("org_level", "department", "manager_user_id", "territories", "financial_clearance",
                   "hr_clearance", "action_limit")


class OrgProfileRequest(BaseModel):
    org_level: OrgLevel = OrgLevel.EMPLOYEE
    department: Department = Department.GENERAL
    manager_user_id: Optional[str] = Field(default=None, max_length=64)
    territories: list[str] = Field(default_factory=list, max_length=MAX_TERRITORIES)
    financial_clearance: bool = False
    hr_clearance: bool = False
    action_limit: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("100000000"))

    model_config = {"extra": "forbid"}

    @field_validator("territories")
    @classmethod
    def clean_territories(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for t in value:
            t = " ".join(str(t).split())
            if not t:
                continue
            if len(t) > 60:
                raise ValueError("Territory names must be 60 characters or fewer")
            if t.lower() not in {x.lower() for x in out}:
                out.append(t)
        return out


def _rows(res) -> list[dict]:
    data = getattr(res, "data", None)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _require_owner(db, user: dict):
    try:
        p = build_principal(db, str(user.get("company_id") or ""), str(user.get("sub") or ""),
                            include_delegations=False)
    except AuthorizationDenied:
        raise HTTPException(status_code=403, detail="Your account is not active for this company") from None
    except AuthorizationUnavailable:
        raise HTTPException(status_code=503, detail="Authorization temporarily unavailable") from None
    if not p.is_owner:
        raise HTTPException(status_code=403, detail="Only a company owner can manage the org chart")
    return p


def _members(db, company_id: str) -> list[dict]:
    try:
        return _rows(db.table("users").select("id,full_name,email,role,is_active")
                     .eq("company_id", company_id).execute())
    except Exception:
        raise HTTPException(status_code=503, detail="Team members are unavailable") from None


def _profiles(db, company_id: str) -> dict[str, dict]:
    try:
        rows = _rows(db.table("lola_org_members").select("*").eq("company_id", company_id).execute())
    except Exception:
        raise HTTPException(status_code=503, detail="The org chart is unavailable") from None
    return {str(r.get("user_id")): r for r in rows}


def _public_profile(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    out = {k: row.get(k) for k in _PROFILE_FIELDS}
    out["territories"] = list(out.get("territories") or [])
    out["action_limit"] = str(row["action_limit"]) if row.get("action_limit") is not None else None
    out["updated_at"] = row.get("updated_at")
    return out


@router.get("")
async def get_org(user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = _require_owner(db, user)
    members = _members(db, p.company_id)
    profiles = _profiles(db, p.company_id)
    by_id = {str(m["id"]): m for m in members}
    out = []
    for m in members:
        prof = profiles.get(str(m["id"]))
        mgr = by_id.get(str((prof or {}).get("manager_user_id") or ""))
        out.append({
            "user_id": str(m["id"]), "name": m.get("full_name") or m.get("email"), "email": m.get("email"),
            "role": m.get("role"), "active": m.get("is_active") is True, "profile": _public_profile(prof),
            "reports_to_name": (mgr.get("full_name") or mgr.get("email")) if mgr else None,
            "allowed_levels": sorted(l.value for l in _ALLOWED_LEVELS.get(m.get("role"), {OrgLevel.EMPLOYEE})),
        })
    out.sort(key=lambda r: (not r["active"], str(r["name"] or "").lower()))
    return {"members": out, "departments": [d.value for d in Department],
            "levels": [l.value for l in OrgLevel]}


def _creates_loop(profiles: dict[str, dict], user_id: str, manager_id: Optional[str]) -> bool:
    seen: set[str] = set()
    current = manager_id
    for _ in range(MAX_CHAIN):
        if not current:
            return False
        if current == user_id:
            return True
        if current in seen:
            return False  # an existing loop elsewhere is not this edit's doing
        seen.add(current)
        current = str((profiles.get(current) or {}).get("manager_user_id") or "") or None
    return True  # implausibly deep chain: refuse rather than guess


@router.put("/{user_id}")
async def put_org_profile(user_id: str, body: OrgProfileRequest, user: dict = Depends(get_current_user),
                          db=Depends(get_db)):
    p = _require_owner(db, user)
    members = {str(m["id"]): m for m in _members(db, p.company_id)}
    target = members.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Team member not found")
    role = str(target.get("role") or "")
    allowed = _ALLOWED_LEVELS.get(role, {OrgLevel.EMPLOYEE})
    if body.org_level not in allowed:
        raise HTTPException(status_code=422, detail=f"A {role.replace('_', ' ')} can't be set to "
                                                    f"{body.org_level.value.replace('_', ' ')}; change their role first")
    if body.org_level == OrgLevel.DEPARTMENT_MANAGER and body.department in (Department.GENERAL,
                                                                             Department.EXECUTIVE):
        raise HTTPException(status_code=422, detail="A department manager needs a specific department")
    manager_id = body.manager_user_id or None
    if manager_id:
        mgr = members.get(manager_id)
        if mgr is None or mgr.get("is_active") is not True:
            raise HTTPException(status_code=422, detail="'Reports to' must be an active member of your company")
        if manager_id == user_id:
            raise HTTPException(status_code=422, detail="Someone can't report to themselves")
    profiles = _profiles(db, p.company_id)
    if _creates_loop(profiles, user_id, manager_id):
        raise HTTPException(status_code=422, detail="That would create a reporting loop")

    before = _public_profile(profiles.get(user_id))
    row = {
        "company_id": p.company_id, "user_id": user_id, "org_level": body.org_level.value,
        "department": body.department.value, "manager_user_id": manager_id, "territories": body.territories,
        "financial_clearance": body.financial_clearance, "hr_clearance": body.hr_clearance,
        "action_limit": str(body.action_limit) if body.action_limit is not None else None,
        "updated_by": p.user_id, "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.table("lola_org_members").upsert(row, on_conflict="company_id,user_id").execute()
    except Exception:
        raise HTTPException(status_code=503, detail="The org chart could not be saved") from None
    after = _public_profile(row)
    write_audit_event(db, company_id=p.company_id, event_type="lola_org_profile_updated", actor_id=p.user_id,
                      before_snapshot=before, after_snapshot=after, metadata={"target_user_id": user_id})
    return {"user_id": user_id, "profile": after}


@router.delete("/{user_id}")
async def delete_org_profile(user_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = _require_owner(db, user)
    profiles = _profiles(db, p.company_id)
    before = profiles.get(user_id)
    if before is None:
        raise HTTPException(status_code=404, detail="No org profile for this person")
    reports = [uid for uid, r in profiles.items() if str(r.get("manager_user_id") or "") == user_id]
    if reports:
        raise HTTPException(status_code=409, detail="Reassign the people who report to them first")
    try:
        db.table("lola_org_members").delete().eq("company_id", p.company_id).eq("user_id", user_id).execute()
    except Exception:
        raise HTTPException(status_code=503, detail="The org chart could not be saved") from None
    write_audit_event(db, company_id=p.company_id, event_type="lola_org_profile_removed", actor_id=p.user_id,
                      before_snapshot=_public_profile(before), metadata={"target_user_id": user_id})
    return {"user_id": user_id, "profile": None}
