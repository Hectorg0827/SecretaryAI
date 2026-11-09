"""
Mocked Lola contracts for Workstreams 2, 3 and 4.

Use these to build UI, voice and new department tools without a database,
QuickBooks or Claude. Every example is a VALID instance of the real contract
models, so anything that renders these will render real responses.

    from app.lola.mocks import MockLola, example_responses
    resp = await MockLola().handle({"sub": "u1", "company_id": "c1"}, LolaRequest(message="..."))

Keyword routing of the mock (case-insensitive):
    "payroll" → denied · "inventory" → unavailable · "email" → pending_approval
    "report" → completed · "fail" → failed · "partial" → partial · otherwise → answered
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.lola.contracts import (
    ActionProposal,
    ActionReceipt,
    BusinessEntity,
    Department,
    EntityType,
    EvidenceRef,
    LolaRequest,
    LolaResponse,
    LolaStatus,
    PermissionDecision,
    ResourceType,
    RiskTier,
    Sensitivity,
    ToolResult,
)

_T = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
_CO = "00000000-0000-0000-0000-0000000000c1"
_USER = "00000000-0000-0000-0000-0000000000a1"

_ACME = BusinessEntity(
    entity_type=EntityType.CUSTOMER, entity_id="QB-101", display_name="Acme Foods",
    department=Department.SALES,
    evidence=[EvidenceRef(source_system="supabase.accounts", record_type="customer", record_id="QB-101",
                          fetched_at=_T)],
)


def _allow(resource: ResourceType, scope: str = "own", redacted: list[str] | None = None) -> PermissionDecision:
    return PermissionDecision(allowed=True, resource_type=resource, action="read", reason="Assigned accounts only",
                              basis=["role:sales_rep", "org_level:employee"], scope=scope,
                              redacted_fields=redacted or [], evaluated_at=_T)


def _deny(resource: ResourceType, reason: str) -> PermissionDecision:
    return PermissionDecision(allowed=False, resource_type=resource, action="read", reason=reason,
                              basis=["role:sales_rep"], scope="none", sensitivity_ceiling=Sensitivity.PUBLIC,
                              evaluated_at=_T)


def _accounts_result() -> ToolResult:
    return ToolResult(
        tool="sales.customer_accounts", department=Department.SALES, status=LolaStatus.ANSWERED,
        summary="1 account(s) in your scope",
        data={"accounts": [{"qb_id": "QB-101", "name": "Acme Foods", "state": "TX"}], "scope": "own"},
        evidence=[EvidenceRef(source_system="quickbooks", record_type="customer", record_id="QB-101",
                              fetched_at=_T)],
        authorization=_allow(ResourceType.CUSTOMER_ACCOUNT, redacted=["balance", "credit_limit"]),
        started_at=_T, finished_at=_T,
    )


def _receipt(status: LolaStatus, tier: RiskTier = RiskTier.APPROVAL_REQUIRED, **kw) -> ActionReceipt:
    return ActionReceipt(
        receipt_id="rcpt-0001", proposal_id="prop-0001", company_id=_CO,
        action_type=kw.pop("action_type", "draft_customer_email"), risk_tier=tier, status=status,
        idempotency_key="lola:example", requested_by=_USER, draft_id=kw.pop("draft_id", "draft-0001"),
        created_at=_T, updated_at=_T, **kw,
    )


def _response(status: LolaStatus, answer: str, **kw) -> LolaResponse:
    return LolaResponse(request_id="req-0001", conversation_id="conv-0001", status=status, answer=answer,
                        speech=kw.pop("speech", answer[:200]), created_at=_T, **kw)


def example_responses() -> dict[str, LolaResponse]:
    acct = _accounts_result()
    proposal = ActionProposal(
        proposal_id="prop-0001", action_type="draft_customer_email", risk_tier=RiskTier.APPROVAL_REQUIRED,
        title="Draft customer email", description="Follow up with Acme Foods about their reorder",
        payload={"account_id": "QB-101", "account_name": "Acme Foods", "instruction": "Follow up"},
        entity=_ACME, requested_by=_USER, company_id=_CO, idempotency_key="lola:example",
        approval_roles=["owner", "manager"], authorization=_allow(ResourceType.CUSTOMER_ACCOUNT),
        created_at=_T,
    )
    return {
        "answered": _response(
            LolaStatus.ANSWERED, "Accounts (1 account(s) in your scope):\n• Acme Foods",
            intents=["account_status"], entities=[_ACME], tool_results=[acct], evidence=acct.evidence,
        ),
        "partial": _response(
            LolaStatus.PARTIAL,
            "Accounts (1 account(s) in your scope):\n• Acme Foods\nI can't share financial information "
            "with your current permissions.",
            intents=["account_status", "receivables"], entities=[_ACME],
            tool_results=[acct, ToolResult(
                tool="finance.receivables", department=Department.FINANCE, status=LolaStatus.DENIED,
                summary="Financial information is restricted to finance and leadership",
                authorization=_deny(ResourceType.RECEIVABLES,
                                    "Financial information is restricted to finance and leadership"),
                sensitivity=Sensitivity.CONFIDENTIAL, error_code="permission_denied", started_at=_T,
                finished_at=_T)],
            evidence=acct.evidence,
            withheld=["I can't share financial information with your current permissions."],
        ),
        "denied": _response(
            LolaStatus.DENIED, "I can't share HR and payroll information with your current permissions.",
            intents=["payroll"],
            tool_results=[ToolResult(
                tool="hr.payroll", department=Department.HR, status=LolaStatus.DENIED,
                summary="Payroll information is restricted",
                authorization=_deny(ResourceType.PAYROLL, "Payroll information is restricted"),
                sensitivity=Sensitivity.RESTRICTED, error_code="permission_denied", started_at=_T,
                finished_at=_T)],
            withheld=["I can't share HR and payroll information with your current permissions."],
        ),
        "unavailable": _response(
            LolaStatus.UNAVAILABLE, "Inventory information is unavailable right now (This data source is not connected).",
            intents=["inventory_check"],
            tool_results=[ToolResult(
                tool="operations.inventory", department=Department.OPERATIONS, status=LolaStatus.UNAVAILABLE,
                summary="This data source is not connected",
                authorization=_allow(ResourceType.INVENTORY, scope="company"),
                error_code="source_not_connected", started_at=_T, finished_at=_T)],
            withheld=["Inventory information is unavailable right now (This data source is not connected)."],
        ),
        "pending_approval": _response(
            LolaStatus.PENDING_APPROVAL, "Prepared and waiting for approval. Nothing has been sent.",
            intents=["draft_email"], entities=[_ACME], proposals=[proposal],
            receipts=[_receipt(LolaStatus.PENDING_APPROVAL)],
        ),
        "completed": _response(
            LolaStatus.COMPLETED, "Done and verified.", intents=["general"],
            receipts=[_receipt(LolaStatus.COMPLETED, RiskTier.ROUTINE_AUTOMATION, action_type="generate_internal_report",
                               draft_id=None, verified=True,
                               external_reference="internal-report:2026-11-09T15:00:00+00:00")],
        ),
        "failed": _response(
            LolaStatus.FAILED, "Your company's action policy could not be checked, so nothing was queued",
            intents=["draft_purchase_order"],
        ),
        "high_risk": _response(
            LolaStatus.PENDING_APPROVAL, "Prepared and waiting for approval. Nothing has been sent.",
            intents=["draft_purchase_order"],
            proposals=[proposal.model_copy(update={
                "action_type": "draft_purchase_order", "risk_tier": RiskTier.HIGH_RISK,
                "title": "Draft purchase order", "amount": Decimal("25000"),
                "requires_independent_approval": True, "entity": None,
                "payload": {"instruction": "Reorder 500 cases", "amount": "25000"},
            })],
            receipts=[_receipt(LolaStatus.PENDING_APPROVAL, RiskTier.HIGH_RISK, action_type="draft_purchase_order")],
        ),
    }


class MockLola:
    """Drop-in stand-in for LolaOrchestrator.handle in other teams' tests."""

    async def handle(self, user: dict, request: LolaRequest) -> LolaResponse:
        ex = example_responses()
        m = request.message.lower()
        for word, key in (("payroll", "denied"), ("inventory", "unavailable"), ("email", "pending_approval"),
                          ("report", "completed"), ("fail", "failed"), ("partial", "partial"),
                          ("high risk", "high_risk")):
            if word in m:
                return ex[key]
        return ex["answered"]
