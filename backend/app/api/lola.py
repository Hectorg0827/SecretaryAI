"""
Lola API — the single entry point clients (web, desktop, mobile, voice) use to
talk to Lion BI's central intelligence.

All endpoints authenticate with the normal session token, then rebuild the
caller's authority from the database (see app/lola/authorization.py). Nothing
in a request body can choose the tenant, the role or the user.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.lola.authorization import (
    NON_DELEGABLE,
    AuthorizationDenied,
    AuthorizationUnavailable,
    Authorizer,
    Principal,
    build_principal,
)
from app.lola.contracts import CONTRACT_MODELS, CONTRACT_VERSION, LolaRequest, LolaResponse, ResourceType
from app.lola.governance import GovernedActionService, receipt_from_row
from app.lola.memory import MEMORY_KINDS, MEMORY_SCOPES, LolaMemoryStore, MemoryRejected, MemoryUnavailable
from app.lola.orchestrator import LolaOrchestrator, narrator_from_env
from app.lola.tools import default_registry
from app.utils.audit import write_audit_event

log = logging.getLogger(__name__)
router = APIRouter()

MAX_DELEGATION_DAYS = 90


def _principal(db, user: dict, *, include_delegations: bool = True) -> Principal:
    try:
        return build_principal(db, str(user.get("company_id") or ""), str(user.get("sub") or ""),
                               include_delegations=include_delegations)
    except AuthorizationDenied:
        raise HTTPException(status_code=403, detail="Your account is not active for this company") from None
    except AuthorizationUnavailable:
        raise HTTPException(status_code=503, detail="Authorization temporarily unavailable") from None


def _adapter_for(user: dict, db):
    """Build the data adapter if connectors are configured; None → tools report 'unavailable'."""
    try:
        from app.api.deps import get_adapter
        return get_adapter(user=user, db=db)
    except Exception:
        log.info("Lola running without a data adapter for this tenant")
        return None


# ─── Ask ─────────────────────────────────────────────────────────────────────


@router.post("/ask", response_model=LolaResponse)
async def ask_lola(request: LolaRequest, user: dict = Depends(get_current_user), db=Depends(get_db)):
    _principal(db, user)  # fail fast with a clean 403/503 before touching sources
    orchestrator = LolaOrchestrator(db, _adapter_for(user, db), narrator=narrator_from_env())
    try:
        return await orchestrator.handle(user, request)
    except AuthorizationDenied:
        raise HTTPException(status_code=403, detail="Your account is not active for this company") from None
    except AuthorizationUnavailable:
        raise HTTPException(status_code=503, detail="Authorization temporarily unavailable") from None


# ─── Introspection & contracts ───────────────────────────────────────────────


@router.get("/me")
async def lola_me(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """What Lola may access for the caller — for UI hints, never for enforcement."""
    p = _principal(db, user)
    auth = Authorizer(p, db)
    ctx = p.to_context(conversation_id="-", request_id=str(uuid.uuid4()), channel="api")
    decisions = [json.loads(auth.can(r).model_dump_json()) for r in ResourceType
                 if r not in (ResourceType.ACTION,)]
    return {"context": json.loads(ctx.model_dump_json()), "permissions": decisions}


@router.get("/tools")
async def lola_tools(user: dict = Depends(get_current_user)):
    return {"contract_version": CONTRACT_VERSION, "tools": default_registry().describe()}


@router.get("/contracts")
async def lola_contracts(user: dict = Depends(get_current_user)):
    from app.lola.mocks import example_responses
    return {
        "contract_version": CONTRACT_VERSION,
        "schemas": {m.__name__: m.model_json_schema() for m in CONTRACT_MODELS},
        "examples": {k: json.loads(v.model_dump_json()) for k, v in example_responses().items()},
    }


# ─── Memory ──────────────────────────────────────────────────────────────────


class RememberRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    scope: str = Field(default="user")
    kind: Optional[str] = None
    entity_id: Optional[str] = Field(default=None, max_length=120)
    ttl_days: Optional[int] = Field(default=None, ge=1, le=3650)


@router.get("/memory")
async def list_memory(user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = _principal(db, user)
    try:
        rows = LolaMemoryStore(db, Authorizer(p, db)).recall(limit=100)
    except MemoryUnavailable:
        raise HTTPException(status_code=503, detail="Memory unavailable") from None
    return {"memories": [LolaMemoryStore.public_view(r) for r in rows]}


@router.post("/memory", status_code=201)
async def add_memory(body: RememberRequest, user: dict = Depends(get_current_user), db=Depends(get_db)):
    if body.scope not in MEMORY_SCOPES or (body.kind and body.kind not in MEMORY_KINDS):
        raise HTTPException(status_code=422, detail="Unknown memory scope or kind")
    p = _principal(db, user)
    store = LolaMemoryStore(db, Authorizer(p, db))
    try:
        row = store.remember(body.content, scope=body.scope, kind=body.kind,
                             entity_type="customer" if body.entity_id else None, entity_id=body.entity_id,
                             ttl_days=body.ttl_days)
    except MemoryRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except MemoryUnavailable:
        raise HTTPException(status_code=503, detail="Memory unavailable") from None
    return {"memory": LolaMemoryStore.public_view(row)}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = _principal(db, user)
    try:
        ok = LolaMemoryStore(db, Authorizer(p, db)).forget(memory_id)
    except MemoryUnavailable:
        raise HTTPException(status_code=503, detail="Memory unavailable") from None
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# ─── Action receipts ─────────────────────────────────────────────────────────


@router.get("/actions")
async def list_actions(limit: int = Query(50, ge=1, le=200), user: dict = Depends(get_current_user),
                       db=Depends(get_db)):
    p = _principal(db, user)
    svc = GovernedActionService(db, Authorizer(p, db))
    try:
        rows = svc.list_receipts(limit)
    except Exception:
        raise HTTPException(status_code=503, detail="Action receipts unavailable") from None
    return {"receipts": [json.loads(receipt_from_row(r).model_dump_json()) for r in rows]}


@router.get("/actions/{receipt_id}")
async def get_action(receipt_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Returns the receipt re-verified against the approval queue and execution ledger."""
    p = _principal(db, user)
    svc = GovernedActionService(db, Authorizer(p, db))
    try:
        row = svc.get_receipt(receipt_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Receipt not found")
        row = svc.refresh(row)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Action receipts unavailable") from None
    return {"receipt": json.loads(receipt_from_row(row).model_dump_json())}


# ─── Delegations ─────────────────────────────────────────────────────────────


class DelegationRequest(BaseModel):
    grantee_user_id: str = Field(min_length=1, max_length=64)
    resource_types: list[ResourceType] = Field(min_length=1, max_length=8)
    account_ids: list[str] = Field(default_factory=list, max_length=200)
    days: int = Field(ge=1, le=MAX_DELEGATION_DAYS)
    reason: str = Field(min_length=3, max_length=300)


@router.get("/delegations")
async def list_delegations(user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = _principal(db, user)
    try:
        given = (db.table("lola_delegations").select("*").eq("company_id", p.company_id)
                 .eq("grantor_user_id", p.user_id).execute()).data or []
        received = (db.table("lola_delegations").select("*").eq("company_id", p.company_id)
                    .eq("grantee_user_id", p.user_id).execute()).data or []
    except Exception:
        raise HTTPException(status_code=503, detail="Delegations unavailable") from None
    return {"given": given, "received": received, "active_received": [d.id for d in p.delegations]}


@router.post("/delegations", status_code=201)
async def create_delegation(body: DelegationRequest, user: dict = Depends(get_current_user), db=Depends(get_db)):
    # The grantor's OWN rights only — delegated rights can never be re-delegated.
    grantor = _principal(db, user, include_delegations=False)
    auth = Authorizer(grantor, db)
    if body.grantee_user_id == grantor.user_id:
        raise HTTPException(status_code=422, detail="You cannot delegate to yourself")
    blocked = [r.value for r in body.resource_types if r in NON_DELEGABLE]
    if blocked:
        raise HTTPException(status_code=403, detail=f"These cannot be delegated: {', '.join(blocked)}")
    for r in body.resource_types:
        if not auth.can(r).allowed:
            raise HTTPException(status_code=403, detail=f"You cannot delegate access you do not have: {r.value}")
    for acct in body.account_ids:
        if not auth.can_see_account(acct):
            raise HTTPException(status_code=403, detail="You can only delegate accounts you can access")
    try:
        build_principal(db, grantor.company_id, body.grantee_user_id, include_delegations=False)
    except AuthorizationDenied:
        raise HTTPException(status_code=404, detail="Team member not found") from None
    except AuthorizationUnavailable:
        raise HTTPException(status_code=503, detail="Authorization temporarily unavailable") from None

    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()), "company_id": grantor.company_id, "grantor_user_id": grantor.user_id,
        "grantee_user_id": body.grantee_user_id, "resource_types": [r.value for r in body.resource_types],
        "account_ids": body.account_ids, "reason": body.reason, "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=body.days)).isoformat(), "revoked_at": None,
    }
    try:
        db.table("lola_delegations").insert(row).execute()
    except Exception:
        raise HTTPException(status_code=503, detail="Delegations unavailable") from None
    write_audit_event(db, company_id=grantor.company_id, event_type="lola_delegation_created",
                      actor_id=grantor.user_id,
                      metadata={"delegation_id": row["id"], "grantee": body.grantee_user_id,
                                "resource_types": row["resource_types"], "expires_at": row["expires_at"]})
    return {"delegation": row}


@router.delete("/delegations/{delegation_id}")
async def revoke_delegation(delegation_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = _principal(db, user, include_delegations=False)
    try:
        rows = (db.table("lola_delegations").select("*").eq("company_id", p.company_id)
                .eq("id", delegation_id).limit(1).execute()).data or []
    except Exception:
        raise HTTPException(status_code=503, detail="Delegations unavailable") from None
    if not rows:
        raise HTTPException(status_code=404, detail="Delegation not found")
    row = rows[0]
    if str(row.get("grantor_user_id")) != p.user_id and not p.is_owner:
        raise HTTPException(status_code=404, detail="Delegation not found")
    db.table("lola_delegations").update({"revoked_at": datetime.now(timezone.utc).isoformat()}).eq(
        "id", delegation_id).eq("company_id", p.company_id).execute()
    write_audit_event(db, company_id=p.company_id, event_type="lola_delegation_revoked", actor_id=p.user_id,
                      metadata={"delegation_id": delegation_id})
    return {"revoked": True}
