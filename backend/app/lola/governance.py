"""
Lola governed actions — built on the EXISTING approval queue (``drafts``),
policy engine (``policy_rules``), autonomy rules (``ActionEngine``) and durable
execution ledger (``agent_executions``).

Lifecycle and what each state really means:

    draft → pending_approval → approved → executing → completed (verified)
                         ↘ rejected          ↘ failed / needs_reconciliation

* Approved ≠ done. Queued ≠ done. ``completed`` is set ONLY when the durable
  execution ledger reports completion WITH an external reference, and the
  receipt is then marked ``verified=True``.
* Every proposal gets a deterministic idempotency key; proposing the same thing
  twice returns the original receipt and never queues a second draft.
* The tenant policy is evaluated at proposal time AND re-evaluated by the
  existing approval endpoint before execution. If the policy source is
  unavailable, sensitive actions are blocked (fail closed).
* Permissions are checked at proposal, re-checked for the approver, and the
  requester's permissions are re-checked at approval time — a requester who has
  since lost access cannot get their stale proposal approved.
* Lola never sends external communications or writes financial records itself.
  External effects can only happen through the existing durable gateway.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.actions.approval_queue import ApprovalQueue
from app.actions.engine import ActionEngine
from app.actions.schemas import ActionValidationError, validate_action_payload
from app.domain.contracts import ActionClass, DataPath
from app.domain.contracts import ActionProposal as DomainActionProposal
from app.domain.policy import PolicyEngine, PolicySourceUnavailable
from app.lola.authorization import (
    AuthorizationDenied,
    AuthorizationUnavailable,
    Authorizer,
    Principal,
    build_principal,
)
from app.lola.contracts import (
    ActionProposal,
    ActionReceipt,
    BusinessEntity,
    EntityType,
    LolaStatus,
    PermissionDecision,
    ResourceType,
    RiskTier,
    Sensitivity,
)
from app.utils.audit import write_audit_event

log = logging.getLogger(__name__)

LOLA_META_KEY = "_lola"
HIGH_RISK_AMOUNT = Decimal("10000")


@dataclass(frozen=True)
class ActionSpecDef:
    action_type: str
    tier: RiskTier
    title: str
    permissions: tuple[str, ...]          # caller needs ANY of these
    action_class: ActionClass
    capability: str
    needs_customer: bool = False
    resource: ResourceType = ResourceType.ACTION
    # action_type stored on the approval-queue draft. Executable drafts use the
    # durable path's type ("email_reply", "purchase_order").
    queue_action_type: Optional[str] = None


CATALOG: dict[str, ActionSpecDef] = {
    "draft_customer_email": ActionSpecDef(
        "draft_customer_email", RiskTier.APPROVAL_REQUIRED, "Draft customer email",
        ("trigger_actions", "view_own_accounts"), ActionClass.COMMIT, "email", needs_customer=True,
        resource=ResourceType.CUSTOMER_ACCOUNT, queue_action_type="email_reply",
    ),
    "draft_purchase_order": ActionSpecDef(
        "draft_purchase_order", RiskTier.APPROVAL_REQUIRED, "Draft purchase order",
        ("draft_purchase_orders",), ActionClass.COMMIT, "purchase_orders",
        resource=ResourceType.PURCHASING, queue_action_type="purchase_order",
    ),
    "generate_internal_report": ActionSpecDef(
        "generate_internal_report", RiskTier.ROUTINE_AUTOMATION, "Generate internal report",
        ("run_reports",), ActionClass.DRAFT, "reports",
    ),
}


def is_prohibited(action_type: str) -> bool:
    """Anything outside Lola's catalogue is refused — including everything the
    existing ActionEngine marks prohibited (payroll, bank, financial writes,
    direct external email, data sharing)."""
    return action_type not in CATALOG or ActionEngine.AUTONOMY_RULES.get(action_type) == "prohibited"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(principal: Principal, action_type: str, payload: dict,
                    client_request_id: Optional[str], conversation_id: Optional[str]) -> str:
    basis = "|".join([
        principal.company_id, principal.user_id, action_type, _canonical(payload),
        client_request_id or "", conversation_id or "",
    ])
    return "lola:" + hashlib.sha256(basis.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(res) -> list[dict]:
    data = getattr(res, "data", None)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def receipt_from_row(row: dict) -> ActionReceipt:
    return ActionReceipt(
        receipt_id=str(row["id"]), proposal_id=str(row.get("proposal_id") or row["id"]),
        company_id=str(row["company_id"]), action_type=row["action_type"],
        risk_tier=RiskTier(row["risk_tier"]), status=LolaStatus(row["status"]),
        idempotency_key=row["idempotency_key"], requested_by=str(row["requested_by"]),
        draft_id=row.get("draft_id"), approved_by=row.get("approved_by"),
        execution_id=row.get("execution_id"), external_reference=row.get("external_reference"),
        verified=bool(row.get("verified")), error_code=row.get("error_code"),
        history=list(row.get("history") or []),
        created_at=row.get("created_at") or _now_iso(), updated_at=row.get("updated_at") or _now_iso(),
    )


def _receipt_entity(row: dict) -> Optional[str]:
    entity = (row.get("proposal") or {}).get("entity") or {}
    return str(entity["entity_id"]) if isinstance(entity, dict) and entity.get("entity_id") else None


def can_review(auth: Authorizer, requested_by: Any, entity_id: Optional[str]) -> bool:
    """May this principal see (and so review) an action requested by someone?"""
    p = auth.p
    if str(requested_by or "") == p.user_id:
        return True
    if not (p.has("approve_actions") or p.is_executive):
        return False
    if entity_id and not auth.can_see_account(entity_id):
        return False
    if p.company_wide:
        return True
    return str(requested_by or "") in (set(p.reports) | set(p.department_members))


def visible_lola_drafts(db, approver_user: dict, drafts: list[dict]) -> list[dict]:
    """Filter the EXISTING pending-approval list: Lola drafts only for eligible reviewers."""
    lola = [d for d in drafts if isinstance((d.get("content") or {}).get(LOLA_META_KEY), dict)]
    if not lola:
        return drafts
    try:
        auth = Authorizer(build_principal(db, str(approver_user.get("company_id")),
                                          str(approver_user.get("sub"))), db)
    except (AuthorizationDenied, AuthorizationUnavailable):
        auth = None  # fail closed: hide Lola drafts, keep legacy behaviour for the rest
    out = []
    for d in drafts:
        meta = (d.get("content") or {}).get(LOLA_META_KEY)
        if not isinstance(meta, dict):
            out.append(d)
        elif auth is not None and can_review(auth, meta.get("requested_by"), meta.get("entity_id")):
            out.append(d)
    return out


@dataclass
class ProposalOutcome:
    status: LolaStatus
    message: str
    decision: PermissionDecision
    proposal: Optional[ActionProposal] = None
    receipt: Optional[ActionReceipt] = None
    duplicate: bool = False


class GovernedActionService:
    def __init__(self, db, authorizer: Authorizer):
        self._db = db
        self._auth = authorizer
        self.p = authorizer.p

    # ── authorisation helpers ────────────────────────────────────────────

    def _decision(self, spec: Optional[ActionSpecDef], action_type: str, allowed: bool, reason: str,
                  basis: Optional[list[str]] = None) -> PermissionDecision:
        return PermissionDecision(
            allowed=allowed, resource_type=spec.resource if spec else ResourceType.ACTION,
            action="draft", reason=reason,
            basis=(basis or []) + [f"role:{self.p.role}", f"action:{action_type}"],
            scope="own" if allowed else "none",
            sensitivity_ceiling=Sensitivity.INTERNAL,
        )

    @staticmethod
    def _authorize_requester(auth: Authorizer, spec: ActionSpecDef,
                             entity_id: Optional[str]) -> tuple[bool, str]:
        p = auth.p
        if not any(p.has(perm) for perm in spec.permissions) and not p.has("view_all"):
            return False, "Your role cannot request this action"
        if spec.resource != ResourceType.ACTION and not auth.can(spec.resource, "read").allowed:
            return False, "You do not have access to the data this action needs"
        if spec.needs_customer:
            if not entity_id:
                return False, "Tell me which of your accounts this is for"
            if not auth.can_see_account(entity_id):
                return False, "I can't find that account among the accounts you can access"
        return True, "Permitted"

    def _tier(self, spec: ActionSpecDef, amount: Optional[Decimal]) -> RiskTier:
        tier = spec.tier
        if amount is not None and tier in (RiskTier.APPROVAL_REQUIRED, RiskTier.ROUTINE_AUTOMATION):
            if amount >= HIGH_RISK_AMOUNT:
                return RiskTier.HIGH_RISK
            if self.p.action_limit is not None and amount > self.p.action_limit:
                return RiskTier.HIGH_RISK
        return tier

    # ── receipts store ───────────────────────────────────────────────────

    def _get_receipt_by_key(self, key: str) -> Optional[dict]:
        rows = _rows(self._db.table("lola_action_receipts").select("*")
                     .eq("company_id", self.p.company_id).eq("idempotency_key", key).limit(1).execute())
        return rows[0] if rows else None

    def get_receipt(self, receipt_id: str) -> Optional[dict]:
        rows = _rows(self._db.table("lola_action_receipts").select("*")
                     .eq("company_id", self.p.company_id).eq("id", receipt_id).limit(1).execute())
        if not rows or not self.can_view_receipt(rows[0]):
            return None
        return rows[0]

    def can_view_receipt(self, row: dict) -> bool:
        """
        Requesters see their own receipts. Company-wide leadership sees all.
        Other approvers (e.g. department managers) see only receipts from their
        reporting line / department AND about records they can see — a receipt
        carries account names and instructions, so it is business data too.
        """
        return can_review(self._auth, row.get("requested_by"), _receipt_entity(row))

    def list_receipts(self, limit: int = 50) -> list[dict]:
        q = (self._db.table("lola_action_receipts").select("*").eq("company_id", self.p.company_id))
        if not (self.p.has("approve_actions") or self.p.is_executive):
            q = q.eq("requested_by", self.p.user_id)
        rows = _rows(q.order("created_at", desc=True).limit(max(limit * 4, 200)).execute())
        return [r for r in rows if self.can_view_receipt(r)][:limit]

    def _transition(self, row: dict, status: LolaStatus, *, event: str, **fields) -> dict:
        history = list(row.get("history") or [])
        history.append({"at": _now_iso(), "event": event, "from": row.get("status"), "to": status.value,
                        **{k: v for k, v in fields.items() if k in ("error_code", "actor")}})
        update = {"status": status.value, "history": history, "updated_at": _now_iso(),
                  **{k: v for k, v in fields.items() if k != "actor"}}
        self._db.table("lola_action_receipts").update(update).eq("id", row["id"]).eq(
            "company_id", self.p.company_id).execute()
        merged = dict(row)
        merged.update(update)
        return merged

    def _audit(self, event_type: str, row: dict, **meta) -> None:
        write_audit_event(
            self._db, company_id=self.p.company_id, event_type=event_type, actor_id=self.p.user_id,
            action_class=row.get("action_type"),
            metadata={"receipt_id": row.get("id"), "risk_tier": row.get("risk_tier"),
                      "status": row.get("status"), **meta},
        )

    # ── proposal ─────────────────────────────────────────────────────────

    async def propose(
        self,
        action_type: str,
        payload: dict,
        *,
        entity: Optional[BusinessEntity] = None,
        amount: Optional[Decimal] = None,
        description: str = "",
        client_request_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        adapter: Any = None,
    ) -> ProposalOutcome:
        spec = None if is_prohibited(action_type) else CATALOG[action_type]
        if spec is None:
            decision = self._decision(None, action_type, False, "This action is not permitted for Lola")
            write_audit_event(self._db, company_id=self.p.company_id, event_type="lola_action_blocked",
                              actor_id=self.p.user_id, action_class=action_type,
                              metadata={"reason": "prohibited_or_unknown"})
            return ProposalOutcome(LolaStatus.DENIED, decision.reason, decision)

        entity_id = entity.entity_id if entity and entity.entity_type == EntityType.CUSTOMER else None
        ok, reason = self._authorize_requester(self._auth, spec, entity_id)
        decision = self._decision(spec, action_type, ok, reason)
        if not ok:
            return ProposalOutcome(LolaStatus.DENIED, reason, decision)

        payload = dict(payload or {})
        try:
            payload = validate_action_payload(action_type, payload)
        except ActionValidationError:
            return ProposalOutcome(LolaStatus.FAILED, "The action details were not valid", decision)

        key = idempotency_key(self.p, action_type, payload, client_request_id, conversation_id)
        try:
            existing = self._get_receipt_by_key(key)
        except Exception:
            return ProposalOutcome(LolaStatus.FAILED, "Action receipts are unavailable; nothing was queued",
                                   decision)
        if existing:
            existing = await self._resume_if_incomplete(existing, spec)
            return ProposalOutcome(LolaStatus(existing["status"]), "This request was already received",
                                   decision, receipt=receipt_from_row(existing), duplicate=True)

        # Turn the request into an executable draft (real recipient / PO lines).
        from app.lola.drafting import DraftIncomplete, compose_customer_email, compose_purchase_order
        try:
            if action_type == "draft_customer_email":
                payload = await compose_customer_email(self._db, self._auth, entity,
                                                       str(payload.get("instruction") or description))
            elif action_type == "draft_purchase_order":
                payload = await compose_purchase_order(adapter, str(payload.get("instruction") or description))
                computed = Decimal(payload["total"])
                amount = max(amount, computed) if amount is not None else computed
        except DraftIncomplete as exc:
            return ProposalOutcome(LolaStatus.FAILED, f"I couldn't prepare this: {exc}. Nothing was queued.",
                                   decision)

        tier = self._tier(spec, amount)

        # Policy: evaluated for the eventual execution, now. Unavailable → block.
        domain_class = spec.action_class if tier != RiskTier.HIGH_RISK else ActionClass.COMMIT
        try:
            policy = await PolicyEngine(self.p.company_id, self._db).evaluate(DomainActionProposal(
                action_type=action_type, action_class=domain_class, description=description or spec.title,
                company_id=self.p.company_id, requested_by=self.p.user_id, capability=spec.capability,
                path=DataPath.API, entity_type=entity.entity_type.value if entity else None,
                entity_id=entity_id, amount=amount, proposed_changes=payload,
            ))
        except PolicySourceUnavailable:
            write_audit_event(self._db, company_id=self.p.company_id, event_type="lola_policy_unavailable",
                              actor_id=self.p.user_id, action_class=action_type, metadata={"tier": tier.value})
            return ProposalOutcome(LolaStatus.FAILED,
                                   "Your company's action policy could not be checked, so nothing was queued",
                                   decision)
        if policy.effect == "deny":
            denied = self._decision(spec, action_type, False, "Blocked by your company's action policy",
                                    [f"policy:{policy.rule_name}"])
            return ProposalOutcome(LolaStatus.DENIED, denied.reason, denied)
        if tier == RiskTier.ROUTINE_AUTOMATION and policy.effect != "allow":
            tier = RiskTier.APPROVAL_REQUIRED

        approval_roles = list(policy.approval_roles or ["owner", "manager"])
        independent = tier == RiskTier.HIGH_RISK
        proposal = ActionProposal(
            proposal_id=str(uuid.uuid4()), action_type=action_type, risk_tier=tier, title=spec.title,
            description=description or spec.title, payload=payload, amount=amount, entity=entity,
            requested_by=self.p.user_id, company_id=self.p.company_id, idempotency_key=key,
            approval_roles=approval_roles, requires_independent_approval=independent,
            authorization=decision.model_copy(update={"basis": decision.basis + [f"policy:{policy.rule_name}"]}),
            evidence=list(entity.evidence) if entity else [],
        )

        row = {
            "id": str(uuid.uuid4()), "company_id": self.p.company_id, "proposal_id": proposal.proposal_id,
            "action_type": action_type, "risk_tier": tier.value, "status": LolaStatus.DRAFT.value,
            "idempotency_key": key, "requested_by": self.p.user_id,
            "draft_id": str(uuid.uuid4()) if tier != RiskTier.ROUTINE_AUTOMATION else None,
            "verified": False, "proposal": json.loads(proposal.model_dump_json()),
            "history": [{"at": _now_iso(), "event": "proposed", "to": "draft"}],
            "created_at": _now_iso(), "updated_at": _now_iso(),
        }
        try:
            self._db.table("lola_action_receipts").insert(row).execute()
        except Exception:
            # Lost a race on the unique (company_id, idempotency_key)?
            try:
                existing = self._get_receipt_by_key(key)
            except Exception:
                existing = None
            if existing:
                return ProposalOutcome(LolaStatus(existing["status"]), "This request was already received",
                                       decision, receipt=receipt_from_row(existing), duplicate=True)
            return ProposalOutcome(LolaStatus.FAILED, "Action receipts are unavailable; nothing was queued",
                                   decision)

        if tier == RiskTier.ROUTINE_AUTOMATION:
            row = await self._run_routine(row, payload)
        else:
            row = await self._enqueue(row, spec, proposal)
        self._audit("lola_action_proposed", row)
        return ProposalOutcome(LolaStatus(row["status"]), self._status_message(row), decision,
                               proposal=proposal, receipt=receipt_from_row(row))

    async def _enqueue(self, row: dict, spec: ActionSpecDef, proposal: ActionProposal) -> dict:
        content = dict(proposal.payload)
        content[LOLA_META_KEY] = {
            "receipt_id": row["id"], "proposal_id": proposal.proposal_id, "risk_tier": proposal.risk_tier.value,
            "requested_by": proposal.requested_by, "approval_roles": proposal.approval_roles,
            "requires_independent_approval": proposal.requires_independent_approval,
            "amount": str(proposal.amount) if proposal.amount is not None else None,
            "entity_id": proposal.entity.entity_id if proposal.entity else None,
            "action_type": spec.action_type,
        }
        try:
            await ApprovalQueue(self._db).enqueue(
                company_id=self.p.company_id, action_type=spec.queue_action_type or spec.action_type,
                content=content,
                created_by=self.p.user_id, draft_id=row["draft_id"],
            )
        except Exception:
            log.warning("Lola could not enqueue draft for receipt %s", row["id"])
            return self._transition(row, LolaStatus.FAILED, event="enqueue_failed", error_code="queue_unavailable")
        return self._transition(row, LolaStatus.PENDING_APPROVAL, event="queued_for_approval")

    async def _resume_if_incomplete(self, row: dict, spec: ActionSpecDef) -> dict:
        """Safe retry: a receipt stuck in 'draft' re-queues ONLY if its draft does not exist."""
        if row.get("status") != LolaStatus.DRAFT.value or not row.get("draft_id"):
            return row
        drafts = _rows(self._db.table("drafts").select("id,status").eq("company_id", self.p.company_id)
                       .eq("id", row["draft_id"]).limit(1).execute())
        if drafts:
            return self._transition(row, LolaStatus.PENDING_APPROVAL, event="recovered_existing_draft")
        proposal = ActionProposal.model_validate(row["proposal"])
        return await self._enqueue(row, spec, proposal)

    async def _run_routine(self, row: dict, payload: dict) -> dict:
        from app.actions.report_actions import generate_internal_report
        row = self._transition(row, LolaStatus.EXECUTING, event="routine_started")
        try:
            result = await generate_internal_report(payload, self.p.company_id)
        except Exception:
            return self._transition(row, LolaStatus.FAILED, event="routine_failed", error_code="executor_error")
        if result.get("status") != "generated" or result.get("company_id") != self.p.company_id:
            return self._transition(row, LolaStatus.NEEDS_RECONCILIATION, event="routine_unverified",
                                    error_code="unverified_result")
        ref = f"internal-report:{result.get('generated_at')}"
        return self._transition(row, LolaStatus.COMPLETED, event="routine_verified",
                                external_reference=ref, verified=True)

    @staticmethod
    def _status_message(row: dict) -> str:
        return {
            "pending_approval": "Prepared and waiting for approval. Nothing has been sent.",
            "completed": "Done and verified.",
            "failed": "This could not be queued. Nothing was sent.",
            "needs_reconciliation": "The outcome could not be confirmed and needs a human check.",
        }.get(row["status"], row["status"])

    # ── verification ────────────────────────────────────────────────────

    def refresh(self, row: dict) -> dict:
        """Re-derive receipt status from the approval queue and execution ledger."""
        if row.get("status") in (LolaStatus.COMPLETED.value, LolaStatus.REJECTED.value) or not row.get("draft_id"):
            return row
        drafts = _rows(self._db.table("drafts").select("id,status,reviewed_by")
                       .eq("company_id", self.p.company_id).eq("id", row["draft_id"]).limit(1).execute())
        if not drafts:
            return row
        draft = drafts[0]
        dstatus = draft.get("status")
        target: Optional[LolaStatus] = None
        fields: dict[str, Any] = {}
        if dstatus == "pending":
            target = LolaStatus.PENDING_APPROVAL
        elif dstatus == "rejected":
            target = LolaStatus.REJECTED
        elif dstatus == "expired":
            target, fields = LolaStatus.FAILED, {"error_code": "approval_expired"}
        elif dstatus in ("approved", "edited_and_approved"):
            fields["approved_by"] = draft.get("reviewed_by")
            execs: list[dict] = []
            try:
                execs = _rows(self._db.table("agent_executions")
                              .select("id,state,external_reference,last_error_code")
                              .eq("company_id", self.p.company_id).eq("draft_id", row["draft_id"])
                              .limit(1).execute())
            except Exception:
                execs = []
            if not execs:
                target = LolaStatus.APPROVED
            else:
                ex = execs[0]
                fields["execution_id"] = ex.get("id")
                state = ex.get("state")
                if state in ("queued", "running"):
                    target = LolaStatus.EXECUTING
                elif state == "completed" and ex.get("external_reference"):
                    target = LolaStatus.COMPLETED
                    fields.update(external_reference=ex["external_reference"], verified=True)
                elif state == "completed":
                    target, fields["error_code"] = LolaStatus.NEEDS_RECONCILIATION, "missing_provider_reference"
                elif state == "failed":
                    target, fields["error_code"] = LolaStatus.FAILED, ex.get("last_error_code") or "execution_failed"
                else:
                    target = LolaStatus.NEEDS_RECONCILIATION
        if target is None or (target.value == row.get("status") and all(row.get(k) == v for k, v in fields.items())):
            return row
        return self._transition(row, target, event="verified_from_ledger", **fields)


# ─── Approval hook used by the EXISTING /api/actions/approve endpoint ────────


def check_lola_approval(db, draft: dict, approver_user: dict,
                        edited_content: Optional[dict] = None) -> Optional[PermissionDecision]:
    """
    Separation-of-duties and stale-permission check for Lola-originated drafts.

    Returns None for drafts that did not come from Lola (existing behaviour is
    unchanged). Otherwise returns a PermissionDecision; the caller must refuse
    approval when ``allowed`` is False. Raises AuthorizationUnavailable when the
    permission source cannot be read (caller → 503, draft stays pending).
    """
    content = draft.get("content") or {}
    meta = content.get(LOLA_META_KEY) if isinstance(content, dict) else None
    if not isinstance(meta, dict):
        return None
    company_id = str(draft.get("company_id") or approver_user.get("company_id"))

    def deny(reason: str) -> PermissionDecision:
        return PermissionDecision(allowed=False, resource_type=ResourceType.ACTION, action="approve",
                                  reason=reason, scope="none")

    try:
        approver = build_principal(db, company_id, str(approver_user.get("sub")))
    except AuthorizationDenied:
        return deny("Approver is not an active member of this company")
    approver_auth = Authorizer(approver, db)
    if not approver.has("approve_actions"):
        return deny("Your current role cannot approve actions")
    roles = meta.get("approval_roles") or ["owner", "manager"]
    if approver.role not in roles:
        return deny("Your role is not an approver for this action")
    requested_by = str(meta.get("requested_by") or "")
    if meta.get("requires_independent_approval"):
        if requested_by == approver.user_id:
            return deny("High-risk actions need approval from someone other than the requester")
        if not approver.is_executive:
            return deny("High-risk actions need approval from leadership")
    amount = meta.get("amount")
    if amount and approver.action_limit is not None and not approver.is_owner:
        try:
            if Decimal(str(amount)) > approver.action_limit:
                return deny("This amount is above your approval limit")
        except Exception:
            return deny("The action amount could not be verified")
    entity_id = meta.get("entity_id")
    if entity_id and not approver_auth.can_see_account(str(entity_id)):
        return deny("You do not have access to the account this action is for")
    if not can_review(approver_auth, requested_by, str(entity_id) if entity_id else None):
        return deny("This request is outside your team")

    # Re-validate the REQUESTER now (stale permissions).
    spec = CATALOG.get(str(meta.get("action_type") or draft.get("action_type")))
    if spec is None:
        return deny("This action type is no longer permitted")
    # Approvers may polish wording or fill a missing vendor, never redirect an
    # email, change PO lines/amounts, or strip governance metadata.
    from app.lola.drafting import locked_fields_changed
    changed = locked_fields_changed(spec.action_type, content, edited_content)
    if changed:
        return deny("These details are locked after drafting: " + ", ".join(sorted(set(changed))))
    if spec.action_type == "draft_purchase_order":
        final = edited_content if edited_content is not None else content
        if not final.get("vendor_id"):
            return deny("Add the vendor before approving this purchase order")
    try:
        requester = build_principal(db, company_id, requested_by)
    except AuthorizationDenied:
        return deny("The person who requested this no longer has access")
    ok, reason = GovernedActionService._authorize_requester(Authorizer(requester, db), spec,
                                                            str(entity_id) if entity_id else None)
    if not ok:
        return deny("The requester no longer has permission for this action")
    return PermissionDecision(allowed=True, resource_type=ResourceType.ACTION, action="approve",
                              reason="Approver authorised", basis=[f"role:{approver.role}"], scope="company")


def record_lola_review(db, draft: dict, reviewer_id: str, approved: bool) -> None:
    """Mirror an approval-queue decision onto the Lola receipt (best effort)."""
    content = draft.get("content") or {}
    meta = content.get(LOLA_META_KEY) if isinstance(content, dict) else None
    if not isinstance(meta, dict) or not meta.get("receipt_id"):
        return
    try:
        rows = _rows(db.table("lola_action_receipts").select("*").eq("company_id", draft.get("company_id"))
                     .eq("id", meta["receipt_id"]).limit(1).execute())
        if not rows:
            return
        row = rows[0]
        status = LolaStatus.APPROVED if approved else LolaStatus.REJECTED
        history = list(row.get("history") or []) + [{
            "at": _now_iso(), "event": "approved" if approved else "rejected",
            "from": row.get("status"), "to": status.value, "actor": reviewer_id,
        }]
        update = {"status": status.value, "history": history, "updated_at": _now_iso()}
        if approved:
            update["approved_by"] = reviewer_id
        db.table("lola_action_receipts").update(update).eq("id", row["id"]).eq(
            "company_id", draft.get("company_id")).execute()
    except Exception:
        log.warning("Could not mirror review onto Lola receipt")
