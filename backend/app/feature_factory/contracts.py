"""
SecretaryAI · Feature Factory — Canonical Contracts
===================================================
These Pydantic models are the SINGLE SOURCE OF TRUTH for the shapes that move
between the AI interpreter, the validator, the engine, the API, and the
frontend. If a shape is not described here, it does not exist.

Why this matters: per our engineering rules, schema/contract drift is the #1
risk. Everything else in this module imports from here. The TypeScript file
`frontend/src/features/featureFactory/types.ts` mirrors these exactly.

Python 3.11+, Pydantic v2.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums — kept identical to the SQL enums in 001_feature_factory.sql
# ---------------------------------------------------------------------------
class FeatureStatus(str, Enum):
    DRAFT = "draft"
    PENDING_CAPABILITY_APPROVAL = "pending_capability_approval"
    DRY_RUN_READY = "dry_run_ready"
    OBSERVE = "observe"
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"
    REJECTED = "rejected"


class RunMode(str, Enum):
    DRY_RUN = "dry_run"   # compute what WOULD happen; change nothing
    OBSERVE = "observe"   # run on schedule, log intentions, change nothing
    ACT = "act"           # actually do it (commit actions still need approval)


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"               # a policy/validator/kill-switch stopped it
    AWAITING_APPROVAL = "awaiting_approval"


# Action classes, ordered by risk. This ordering drives the policy engine.
class ActionClass(str, Enum):
    READ = "read"               # safe: only reads data
    DRAFT = "draft"             # prepares something for a human (e.g. an email draft)
    COMMIT = "commit"           # changes business state -> REQUIRES APPROVAL
    DESTRUCTIVE = "destructive" # deletes/irreversible -> REQUIRES APPROVAL + snapshot


# ---------------------------------------------------------------------------
# Capabilities — the "contract" a human approves (Layer 1)
# ---------------------------------------------------------------------------
class Capability(BaseModel):
    """One thing a feature is allowed to do, in machine + human form."""
    capability_key: str = Field(..., examples=["invoices.read", "flags.write"])
    resource: str = Field(..., examples=["invoices", "customers"])
    scope: ActionClass
    # Plain-English line the user actually reads and approves.
    human_summary: str = Field(..., examples=["Read your invoices"])


# ---------------------------------------------------------------------------
# The executable Tier 1 spec — pure DATA, never code.
# The engine interprets this with trusted handlers. There is no eval anywhere.
# ---------------------------------------------------------------------------
class TriggerKind(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    EVENT = "event"


class Trigger(BaseModel):
    kind: TriggerKind = TriggerKind.MANUAL
    cron: Optional[str] = None          # required when kind == schedule
    event: Optional[str] = None         # required when kind == event


class Condition(BaseModel):
    field: str                          # e.g. "amount", "last_order_date"
    op: Literal["<", "<=", "==", "!=", ">=", ">", "contains", "in", "older_than_days"]
    value: Any


class Action(BaseModel):
    # Only these action types exist. The validator rejects anything else.
    type: Literal[
        "flag",          # mark a record for attention (commit)
        "notify",        # send an in-app/email alert to a user (draft/commit)
        "create_task",   # create a follow-up task (commit)
        "draft_email",   # prepare an email for human review (draft)
        "summarize",     # produce a read-only summary (read)
        "tag",           # add a non-destructive label (commit)
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class FeatureSpec(BaseModel):
    """The complete, executable definition of a Tier 1 feature."""
    trigger: Trigger = Field(default_factory=Trigger)
    source_entity: str = Field(..., examples=["invoices", "customers", "orders"])
    source_filter: dict[str, Any] = Field(default_factory=dict)
    conditions: list[Condition] = Field(default_factory=list)
    actions: list[Action] = Field(..., min_length=1)

    @field_validator("actions")
    @classmethod
    def _at_least_one_action(cls, v: list[Action]) -> list[Action]:
        if not v:
            raise ValueError("a feature must declare at least one action")
        return v


# ---------------------------------------------------------------------------
# What the interpreter returns from a plain-English request
# ---------------------------------------------------------------------------
class InterpretedFeature(BaseModel):
    suggested_name: str
    description: str                    # plain-English: what it does
    tier: Literal[1, 2, 3]
    spec: FeatureSpec
    declared_capabilities: list[Capability]
    # If the model is unsure, it says so instead of guessing.
    clarifying_question: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


# ---------------------------------------------------------------------------
# Validation result (Layer 3)
# ---------------------------------------------------------------------------
class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str


class ValidationResult(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# A single planned/executed action inside a run
# ---------------------------------------------------------------------------
class PlannedAction(BaseModel):
    action_type: str
    action_class: ActionClass
    target_ref: str                     # e.g. "invoice:INV-1042"
    human_summary: str                  # "Would flag invoice INV-1042 ($6,300)"
    params: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


# ---------------------------------------------------------------------------
# API request/response shapes
# ---------------------------------------------------------------------------
class CreateFeatureRequest(BaseModel):
    request_text: str = Field(..., min_length=3, max_length=2000)


class FeatureView(BaseModel):
    """What the API returns to the frontend for one feature."""
    id: UUID
    name: str
    description: str
    request_text: str
    tier: int
    status: FeatureStatus
    declared_capabilities: list[Capability]
    trigger_kind: str
    schedule_cron: Optional[str]
    observe_runs_required: int
    successful_observe_runs: int
    version: int
    created_at: datetime
    updated_at: datetime


class RunView(BaseModel):
    id: UUID
    feature_id: UUID
    mode: RunMode
    status: RunStatus
    trigger: str
    planned_actions: list[PlannedAction]
    executed_actions: list[PlannedAction]
    result_summary: Optional[str]
    error: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]


class ApprovalDecision(BaseModel):
    approve: bool
    note: Optional[str] = None
