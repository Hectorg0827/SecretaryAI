"""
Lola shared integration contracts (Workstream 1 → Workstreams 2, 3, 4).

These are the ONLY shapes other teams should depend on when they talk to Lola,
whether they build UI (web/desktop/mobile), voice, or new department
capabilities ("tools"). Everything is a Pydantic model so it serialises to the
JSON Schema published at ``docs/lola/contracts.schema.json`` (regenerate with
``python scripts/export_lola_contracts.py``; a test fails if it drifts).

Design rules that every consumer can rely on:

* Every result carries an explicit ``status`` from :class:`LolaStatus`. There is
  no "success-shaped" response without a status, and an empty list is never a
  substitute for "unavailable" or "denied".
* Every fact Lola states can be traced to an :class:`EvidenceRef` (source,
  record, timestamp). Lola never presents data that has no evidence.
* Every tool call records the :class:`PermissionDecision` that allowed or
  refused it, so the UI can explain *why* something is missing.
* An action is never "done" because it was approved or queued. Only an
  :class:`ActionReceipt` in state ``completed`` with ``verified=True`` means the
  side effect was confirmed.

Versioning: bump ``CONTRACT_VERSION`` (semver) on any change. Additive optional
fields → minor. Renames/removals/meaning changes → major.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Enumerations ─────────────────────────────────────────────────────────────


class LolaStatus(str, Enum):
    """Explicit outcome state shared by responses, tool results and actions."""

    ANSWERED = "answered"                  # complete, authorised answer
    PARTIAL = "partial"                    # some parts answered, some withheld/unavailable
    UNAVAILABLE = "unavailable"            # source down / not connected / no data path
    DENIED = "denied"                      # caller is not authorised
    DRAFT = "draft"                        # an action draft was prepared, nothing sent
    PENDING_APPROVAL = "pending_approval"  # waiting in the approval queue
    APPROVED = "approved"                  # approved, NOT yet executed
    EXECUTING = "executing"                # handed to the durable executor
    COMPLETED = "completed"                # executed AND verified
    FAILED = "failed"                      # execution failed or was blocked at execution
    REJECTED = "rejected"                  # a human rejected the draft
    NEEDS_RECONCILIATION = "needs_reconciliation"  # outcome uncertain; human must check


class Sensitivity(str, Enum):
    """Data classification. Ordered: each level includes the ones before it."""

    PUBLIC = "public"              # safe for anyone in the company
    INTERNAL = "internal"          # normal business data, still permission-scoped
    CONFIDENTIAL = "confidential"  # financials, margins, costs, receivables
    RESTRICTED = "restricted"      # HR, payroll, personal data


SENSITIVITY_RANK: dict[Sensitivity, int] = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


class Department(str, Enum):
    EXECUTIVE = "executive"
    SALES = "sales"
    FINANCE = "finance"
    OPERATIONS = "operations"      # inventory, warehouse, logistics
    PURCHASING = "purchasing"
    HR = "hr"
    COMPLIANCE = "compliance"
    GENERAL = "general"


class OrgLevel(str, Enum):
    EXECUTIVE = "executive"
    DEPARTMENT_MANAGER = "department_manager"
    EMPLOYEE = "employee"


class ResourceType(str, Enum):
    """What kind of business data a permission decision is about."""

    CUSTOMER_ACCOUNT = "customer_account"
    SALES = "sales"
    RECEIVABLES = "receivables"
    INVENTORY = "inventory"
    INVENTORY_COST = "inventory_cost"
    PURCHASING = "purchasing"
    FINANCIALS = "financials"
    HR = "hr"
    PAYROLL = "payroll"
    MEMORY = "memory"
    ACTION = "action"


class RiskTier(str, Enum):
    """Governance tier of an action Lola can propose."""

    READ_ONLY = "read_only"                  # no side effects
    DRAFT = "draft"                          # prepares content only
    APPROVAL_REQUIRED = "approval_required"  # a permitted human must approve
    ROUTINE_AUTOMATION = "routine_automation"  # pre-authorised internal automation
    HIGH_RISK = "high_risk"                  # independent approver (not the requester)
    PROHIBITED = "prohibited"                # never, in code


class EntityType(str, Enum):
    CUSTOMER = "customer"
    PRODUCT = "product"
    VENDOR = "vendor"
    EMPLOYEE = "employee"
    PURCHASE_ORDER = "purchase_order"
    INVOICE = "invoice"
    TERRITORY = "territory"
    DEPARTMENT = "department"


class _Model(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")


# ─── Building blocks ─────────────────────────────────────────────────────────


class EvidenceRef(_Model):
    """Where a fact came from. Every stated fact must point at one of these."""

    source_system: str = Field(description="e.g. quickbooks_online, supabase.accounts, lola_memory")
    record_type: str = Field(description="e.g. customer, invoice, inventory_item")
    record_id: Optional[str] = Field(default=None, description="Source-system identifier, if any")
    fetched_at: datetime = Field(default_factory=utcnow)
    data_path: str = Field(default="api", description="api | browser | file | computer_use | cache")
    confidence: int = Field(default=100, ge=0, le=100)
    is_stale: bool = False
    note: Optional[str] = None


class BusinessEntity(_Model):
    """A business object Lola recognised in the conversation (only if authorised)."""

    entity_type: EntityType
    entity_id: str
    display_name: str
    department: Department = Department.GENERAL
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    match_confidence: int = Field(default=100, ge=0, le=100)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class PermissionDecision(_Model):
    """The outcome of a server-side authorisation check."""

    allowed: bool
    resource_type: ResourceType
    action: str = Field(description="read | draft | approve | execute | remember | delegate")
    reason: str
    basis: list[str] = Field(
        default_factory=list,
        description="Which grants applied, e.g. role:manager, reports_to, territory:West, delegation:<id>",
    )
    scope: str = Field(default="none", description="company | department | team | own | delegated | none")
    sensitivity_ceiling: Sensitivity = Sensitivity.INTERNAL
    redacted_fields: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=utcnow)
    policy_version: str = CONTRACT_VERSION


class ToolResult(_Model):
    """Structured return value of every Lola tool (department capability)."""

    tool: str
    department: Department
    status: LolaStatus
    summary: str = Field(description="One-line, user-safe description of the outcome")
    data: Any = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    authorization: PermissionDecision
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)
    error_code: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class ActionProposal(_Model):
    """An action Lola has prepared. Creating one never causes a side effect."""

    proposal_id: str
    action_type: str
    risk_tier: RiskTier
    title: str
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    amount: Optional[Decimal] = None
    entity: Optional[BusinessEntity] = None
    requested_by: str
    company_id: str
    idempotency_key: str
    approval_roles: list[str] = Field(default_factory=list)
    requires_independent_approval: bool = False
    authorization: PermissionDecision
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class ActionReceipt(_Model):
    """Durable record of an action's lifecycle. The source of truth for 'did it happen?'."""

    receipt_id: str
    proposal_id: str
    company_id: str
    action_type: str
    risk_tier: RiskTier
    status: LolaStatus
    idempotency_key: str
    requested_by: str
    draft_id: Optional[str] = None
    approved_by: Optional[str] = None
    execution_id: Optional[str] = None
    external_reference: Optional[str] = None
    verified: bool = Field(
        default=False,
        description="True ONLY when the executor confirmed the side effect (e.g. provider id)",
    )
    error_code: Optional[str] = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ─── Request / response ──────────────────────────────────────────────────────


class LolaContext(_Model):
    """
    Server-built identity + authority context for one request.

    Consumers NEVER construct this from client input: it is derived from the
    verified session token and re-read from the database on every request, so
    a stale or forged role in a token cannot widen access.
    """

    company_id: str
    user_id: str
    role: str
    org_level: OrgLevel
    department: Department
    territories: list[str] = Field(default_factory=list)
    reports: list[str] = Field(default_factory=list, description="user ids in this user's reporting tree")
    financial_clearance: bool = False
    hr_clearance: bool = False
    action_limit: Optional[Decimal] = None
    active_delegation_ids: list[str] = Field(default_factory=list)
    conversation_id: str
    channel: str = "chat"
    request_id: str
    org_profile_available: bool = True
    built_at: datetime = Field(default_factory=utcnow)


class LolaRequest(_Model):
    """What a client (web, desktop, mobile, voice) sends to Lola."""

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None
    channel: str = Field(default="chat", pattern="^(chat|voice|mobile|desktop|api)$")
    client_request_id: Optional[str] = Field(
        default=None, max_length=120,
        description="Client idempotency key. Re-sending the same id never duplicates an action.",
    )
    allow_actions: bool = Field(default=True, description="False = answer only, never draft actions")
    language: str = Field(default="auto", pattern="^(auto|en|es)$",
                          description="Answer language; 'auto' detects English or Spanish from the message")


class LolaResponse(_Model):
    """What Lola returns. Voice clients should speak ``speech`` (already redacted)."""

    contract_version: str = CONTRACT_VERSION
    request_id: str
    conversation_id: str
    status: LolaStatus
    answer: str
    speech: str = Field(description="Short, voice-safe version of the answer")
    intents: list[str] = Field(default_factory=list)
    entities: list[BusinessEntity] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    proposals: list[ActionProposal] = Field(default_factory=list)
    receipts: list[ActionReceipt] = Field(default_factory=list)
    withheld: list[str] = Field(
        default_factory=list,
        description="User-safe notes about what was not shown and why (never the hidden content)",
    )
    memory_used: int = 0
    language: str = Field(default="en", pattern="^(en|es)$", description="Language the answer is written in")
    created_at: datetime = Field(default_factory=utcnow)


CONTRACT_MODELS: tuple[type[BaseModel], ...] = (
    LolaContext,
    LolaRequest,
    LolaResponse,
    BusinessEntity,
    EvidenceRef,
    ToolResult,
    ActionProposal,
    ActionReceipt,
    PermissionDecision,
)


def combine_statuses(statuses: list[LolaStatus]) -> LolaStatus:
    """Overall status of a multi-part answer. Never reports more success than earned."""
    if not statuses:
        return LolaStatus.UNAVAILABLE
    answered = sum(1 for s in statuses if s == LolaStatus.ANSWERED)
    if answered == len(statuses):
        return LolaStatus.ANSWERED
    if answered:
        return LolaStatus.PARTIAL
    if all(s == LolaStatus.DENIED for s in statuses):
        return LolaStatus.DENIED
    if LolaStatus.PARTIAL in statuses:
        return LolaStatus.PARTIAL
    if LolaStatus.DENIED in statuses and LolaStatus.UNAVAILABLE not in statuses:
        return LolaStatus.DENIED
    return LolaStatus.UNAVAILABLE
