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
    receipt = receipt_from_row(row)
    # "client_receipt" is the Workstream 4 shape, so the UI can poll a
    # "submitted" receipt until the ledger verifies (or fails) it.
    from app.lola.client_events import client_receipt
    return {"receipt": json.loads(receipt.model_dump_json()), "client_receipt": client_receipt(receipt)}


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


# ═══ Workstream 4 client contract (docs/lola/CONTRACT.md) ═══════════════════
#
# The web / mobile / desktop Lola UI speaks this contract. These endpoints are
# thin adapters over the same orchestrator, authorisation and governance used
# by /ask — they add no new data paths and no new permissions.

from typing import Literal  # noqa: E402

from fastapi.responses import StreamingResponse  # noqa: E402

from app.lola import client_events  # noqa: E402
from app.lola.contracts import ActionProposal, LolaStatus  # noqa: E402
from app.lola.governance import check_lola_approval, visible_lola_drafts  # noqa: E402
from app.lola.tools import ToolContext  # noqa: E402


class TurnAttachment(BaseModel):
    name: str = Field(max_length=255)
    size: int = Field(ge=0)
    mime: str = Field(max_length=120)
    text: Optional[str] = Field(default=None, max_length=200_000)


class TurnRetention(BaseModel):
    transcript: Literal["none", "session", "saved"] = "session"
    audio: Literal["none", "30d"] = "none"


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    language: Literal["en", "es"] = "en"
    channel: Literal["text", "voice"] = "text"
    supersedes_turn: bool = False
    attachments: list[TurnAttachment] = Field(default_factory=list, max_length=5)
    context_route: Optional[str] = Field(default=None, max_length=200)
    retention: TurnRetention = Field(default_factory=TurnRetention)
    client_request_id: Optional[str] = Field(default=None, max_length=120)


def _sse(events: list[dict]):
    for ev in events:
        yield "data: " + json.dumps(ev, default=str) + "\n\n"


def _draft_for(db, company_id: str, draft_id: Optional[str]) -> Optional[dict]:
    if not draft_id:
        return None
    rows = (db.table("drafts").select("*").eq("company_id", company_id).eq("id", draft_id)
            .limit(1).execute()).data or []
    return rows[0] if rows else None


def _preview_for(db, user: dict, receipt) -> dict:
    """ActionPreview with the server's verdict on whether THIS user may approve."""
    can, note = False, "Waiting for a manager or owner to approve."
    try:
        draft = _draft_for(db, receipt.company_id, receipt.draft_id)
        decision = check_lola_approval(db, draft, user) if draft else None
        if decision is not None:
            can, note = decision.allowed, (None if decision.allowed else decision.reason)
    except (AuthorizationDenied, AuthorizationUnavailable):
        can, note = False, "Approval rights could not be checked right now."
    proposal = None
    try:
        rows = (db.table("lola_action_receipts").select("proposal").eq("company_id", receipt.company_id)
                .eq("id", receipt.receipt_id).limit(1).execute()).data or []
        if rows and rows[0].get("proposal"):
            proposal = ActionProposal.model_validate(rows[0]["proposal"])
    except Exception:
        proposal = None
    return client_events.action_preview(receipt, proposal, can_approve=can, approval_note=note)


@router.post("/turn")
async def lola_turn(body: TurnRequest, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """One conversational turn as a ``text/event-stream`` of WS4 ``LolaEvent`` objects."""
    _principal(db, user)  # clean 403/503 before streaming
    events: list[dict] = []
    try:
        request = LolaRequest(
            message=body.message, conversation_id=body.conversation_id,
            channel="voice" if body.channel == "voice" else "chat",
            client_request_id=body.client_request_id,
        )
        orchestrator = LolaOrchestrator(db, _adapter_for(user, db), narrator=narrator_from_env())
        resp = await orchestrator.handle(user, request, persist=body.retention.transcript != "none")
        previews = [_preview_for(db, user, r) for r in resp.receipts
                    if r.status not in (LolaStatus.COMPLETED,)]
        events = client_events.turn_events(resp, previews, suggestions=client_events.suggestions_for(resp))
        extra: list[dict] = []
        if body.attachments:
            extra.append({"type": "notice", "notice": {
                "kind": "info",
                "message": "Attachments aren't read by Lola yet, so I answered from your business data only."}})
        if body.language == "es":
            extra.append({"type": "notice", "notice": {
                "kind": "info", "message": "Las respuestas en español llegarán pronto; respondí en inglés."}})
        if extra:
            events = events[:-1] + extra + events[-1:]
    except (AuthorizationDenied, AuthorizationUnavailable):
        events = [{"type": "error", "message": "Lola couldn't check your permissions. Nothing was executed.",
                   "retryable": True}, {"type": "done"}]
    except Exception:
        log.exception("Lola turn failed")
        events = [{"type": "error", "message": "Something went wrong. Nothing was executed.", "retryable": True},
                  {"type": "done"}]
    return StreamingResponse(_sse(events), media_type="text/event-stream")


def _receipt_or_404(db, user: dict, receipt_id: str):
    p = _principal(db, user)
    svc = GovernedActionService(db, Authorizer(p, db))
    try:
        row = svc.get_receipt(receipt_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Action receipts unavailable") from None
    if row is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return svc, row


@router.post("/actions/{receipt_id}/approve")
async def lola_approve(receipt_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """
    Approve via the EXISTING approval endpoint logic (Lola separation-of-duties
    checks, tenant policy, durable gateway). Returns a WS4 ``Receipt`` that is
    never "executed" unless the execution ledger verified it.
    """
    from app.api.actions import ApprovalRequest, approve_draft

    svc, row = _receipt_or_404(db, user, receipt_id)
    receipt = receipt_from_row(row)
    if not receipt.draft_id or receipt.status != LolaStatus.PENDING_APPROVAL:
        return client_events.client_receipt(receipt_from_row(svc.refresh(row)))
    draft = _draft_for(db, receipt.company_id, receipt.draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Action not found")
    try:
        decision = check_lola_approval(db, draft, user)
    except AuthorizationUnavailable:
        raise HTTPException(status_code=503, detail="Approval authorization unavailable; nothing was approved") from None
    if decision is None or not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason if decision else "Not a Lola action")
    try:
        await approve_draft(receipt.draft_id, ApprovalRequest(), user=user, db=db,
                            adapter=_adapter_for(user, db))
    except HTTPException as exc:
        if exc.status_code == 409:
            return client_events.client_receipt(
                receipt, "Approval is recorded only when this action type can be executed safely. "
                         "It isn't enabled yet, so nothing was approved or sent.") | {"status": "failed"}
        raise
    return client_events.client_receipt(receipt_from_row(svc.refresh(svc.get_receipt(receipt_id) or row)))


class RejectBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)


@router.post("/actions/{receipt_id}/reject")
async def lola_reject(receipt_id: str, body: RejectBody = RejectBody(), user: dict = Depends(get_current_user),
                      db=Depends(get_db)):
    from app.api.actions import RejectionRequest, reject_draft

    svc, row = _receipt_or_404(db, user, receipt_id)
    receipt = receipt_from_row(row)
    if not receipt.draft_id or receipt.status != LolaStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=409, detail="This action is no longer awaiting approval")
    draft = _draft_for(db, receipt.company_id, receipt.draft_id)
    p = _principal(db, user)
    # The requester may withdraw their own request; otherwise the approver rules apply.
    if receipt.requested_by != p.user_id:
        decision = check_lola_approval(db, draft, user) if draft else None
        if decision is None or not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.reason if decision else "Not a Lola action")
    elif not p.has("approve_actions"):
        # Withdraw without the approver-only endpoint: mark the draft rejected directly.
        db.table("drafts").update({"status": "rejected", "reviewed_by": p.user_id}).eq(
            "id", receipt.draft_id).eq("company_id", p.company_id).eq("status", "pending").execute()
        from app.lola.governance import record_lola_review
        record_lola_review(db, draft, p.user_id, approved=False)
        write_audit_event(db, company_id=p.company_id, event_type="lola_action_withdrawn", actor_id=p.user_id,
                          action_class=receipt.action_type, metadata={"receipt_id": receipt_id})
        return {"action_id": receipt_id, "status": "rejected"}
    await reject_draft(receipt.draft_id, RejectionRequest(reason=body.reason), user=user, db=db)
    return {"action_id": receipt_id, "status": "rejected"}


def _section(sid: str, title: str, result=None, items=None, *, availability=None, note=None, sensitivity=None):
    sec = {"id": sid, "title": title, "items": items or []}
    if result is not None:
        sec["availability"] = client_events.availability_for(result)
        sec["evidence"] = client_events.evidence_for(result, 3)
        if result.status != LolaStatus.ANSWERED:
            sec["availability_note"] = result.summary
        sec["sensitivity"] = client_events._SENSITIVITY.get(result.sensitivity, "standard")
    else:
        sec["availability"] = availability or "ok"
    if note:
        sec["availability_note"] = note
    if sensitivity:
        sec["sensitivity"] = sensitivity
    return sec


@router.get("/briefing")
async def lola_briefing(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Role- and record-scoped command-center briefing built from Lola tools."""
    p = _principal(db, user)
    auth = Authorizer(p, db)
    reg = default_registry()
    ctx = ToolContext(principal=p, authorizer=auth, db=db, adapter=_adapter_for(user, db),
                      request_id=str(uuid.uuid4()))

    async def run(name, args=None):
        return await reg.get(name).run(ctx, args or {})

    sales = await run("sales.performance")
    inventory = await run("operations.inventory")
    receivables = await run("finance.receivables")
    logistics = await run("logistics.assistant", {"question": "Which shipments are delayed?"})

    sections: list[dict] = []
    low = (inventory.data or {}).get("low_stock", []) if inventory.status == LolaStatus.ANSWERED else []
    critical = [{"id": f"low-{i}", "title": f"{name} is at or below its reorder point", "severity": "critical",
                 "href": "/inventory", "ask": f"What should we do about {name}?"} for i, name in enumerate(low[:5])]
    if receivables.status == LolaStatus.ANSWERED and (receivables.data or {}).get("overdue_invoices"):
        d = receivables.data
        critical.append({"id": "ar-overdue", "title": f"{d['overdue_invoices']} overdue invoices "
                         f"({client_events._money(d['overdue_balance'])})", "severity": "warning",
                         "href": "/accounts", "ask": "Who should we prioritise for collections?"})
    sections.append(_section("critical_alerts", "Critical alerts", inventory, critical))
    if sales.status == LolaStatus.ANSWERED:
        d = sales.data
        perf = [{"id": "sales-30d", "title": f"{d['order_count']} orders, {client_events._money(d['total_revenue'])}",
                 "detail": f"Last {d['days']} days · {d['scope']} scope", "severity": "info",
                 "ask": "How do sales compare with last month?"}]
        perf += [{"id": f"top-{i}", "title": t["name"], "meta": client_events._money(t["revenue"])}
                 for i, t in enumerate(d.get("top_accounts", [])[:3])]
    else:
        perf = []
    sections.append(_section("performance", "Performance", sales, perf))
    sections.append(_section("department_priorities", "Department priorities", inventory,
                             [{"id": "inv", "title": inventory.summary, "href": "/inventory"}]
                             if inventory.status == LolaStatus.ANSWERED else []))
    sections.append(_section("financial_risks", "Financial risks", receivables,
                             [{"id": "ar", "title": receivables.summary, "href": "/accounts", "severity": "warning"}]
                             if receivables.status == LolaStatus.ANSWERED else [], sensitivity="financial"))
    sections.append(_section("logistics_exceptions", "Logistics exceptions", logistics,
                             [{"id": "log", "title": logistics.summary[:200], "href": "/logistics",
                               "ask": "Which shipments are delayed?"}]
                             if logistics.status in (LolaStatus.ANSWERED, LolaStatus.PARTIAL) else []))

    svc = GovernedActionService(db, auth)
    if p.has("approve_actions"):
        try:
            from app.actions.approval_queue import ApprovalQueue
            drafts = visible_lola_drafts(db, user, await ApprovalQueue(db).get_pending(p.company_id))
            items = [{"id": d["id"], "title": str(d.get("action_type", "")).replace("_", " ").capitalize(),
                      "detail": str((d.get("content") or {}).get("instruction", ""))[:140], "href": "/work"}
                     for d in drafts[:6]]
            sections.append(_section("pending_approvals", "Pending approvals", items=items))
        except Exception:
            sections.append(_section("pending_approvals", "Pending approvals", availability="unavailable",
                                     note="The approval queue is unavailable right now."))
    try:
        mine = [r for r in svc.list_receipts(20) if str(r.get("requested_by")) == p.user_id]
        recent = [{"id": r["id"], "title": r["action_type"].replace("_", " ").capitalize(),
                   "meta": client_events.client_receipt(receipt_from_row(r))["status"]} for r in mine[:6]]
        sections.append(_section("recent_tasks", "Recent tasks", items=recent))
    except Exception:
        sections.append(_section("recent_tasks", "Recent tasks", availability="unavailable",
                                 note="Task history is unavailable right now."))
    sections.append(_section("next_actions", "Next actions", items=[
        {"id": "na-1", "title": "Review accounts that need attention", "ask": "Which of my accounts need attention?"},
        {"id": "na-2", "title": "Check stock that is running low", "ask": "What's running low in inventory?"},
    ]))
    # Morning briefing = the top item from each of the first sections that has one.
    highlights = [{"id": f"mb-{s['id']}", "title": s["items"][0]["title"]}
                  for s in sections[:3] if s.get("items")]
    sections.append(_section("morning_briefing", "Morning briefing", items=highlights))

    statuses = [sales.status, inventory.status, receivables.status]
    headline = ("Here's where things stand." if LolaStatus.ANSWERED in statuses
                else "Some business data isn't available right now.")
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "greeting": "Good day",
            "headline": headline, "sections": sections}
