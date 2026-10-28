"""§21 governance check — preview a proposed access against the policy rules.

The caller describes who (a governance role) wants to do what (view, export,
approve …) with which data fields, plus optional activity numbers. The §21
module classifies each field, applies the authority matrix, runs the anomaly
detectors and returns the retention rules that apply. Nothing is stored,
logged as an anomaly, blocked or granted: it is a what-if over user input.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.api.insights._common import advisory_response
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.governance import (
    AUTHORITY_MATRIX,
    DATA_LEVEL_LABELS,
    RETENTION_SCHEDULE,
    Action,
    DataLevel,
    GovernanceEngine,
    Role,
    check_permission,
    classify_field,
)

PREFIX = "/governance-check"
TAG = "insights: governance"

router = APIRouter()

GovernanceRole = Literal["viewer", "analyst", "manager", "executive", "system_admin"]
GovernanceAction = Literal["view", "draft", "approve", "execute", "delete", "export", "configure"]
RetentionType = Literal[
    "orders", "invoices", "customer_pii", "action_audit_log", "chat_conversations",
    "report_snapshots", "session_logs", "supplier_agreements",
]
FieldName = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=2, max_length=64,
    pattern=r"^[A-Za-z][A-Za-z0-9_ .-]*$",
)]
Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Activity(_Strict):
    """Optional recent-activity numbers for the anomaly detectors."""
    exports_last_hour: Optional[Count] = None
    access_hour: Optional[Annotated[int, Field(strict=True, ge=0, le=23)]] = None
    usual_for_this_user: bool = False
    records_modified_last_minute: Optional[Count] = None
    failed_logins_last_5_min: Optional[Count] = None


class GovernanceCheckRequest(_Strict):
    role: GovernanceRole
    action: GovernanceAction
    fields: list[FieldName] = Field(min_length=1, max_length=50)
    data_types: list[RetentionType] = Field(default_factory=list, max_length=8)
    activity: Optional[Activity] = None

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, value: list[str]) -> list[str]:
        if len({v.lower() for v in value}) != len(value):
            raise ValueError("Each data field may be listed once")
        return value

    @field_validator("data_types")
    @classmethod
    def unique_types(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Each data type may be listed once")
        return value


_SUBJECT = "proposed-user"  # the detectors need an id; no real user is named


def _anomalies(engine: GovernanceEngine, role: Role, action: Action,
               activity: Optional[Activity], level: DataLevel,
               blocked_level: Optional[DataLevel]) -> list[dict[str, Any]]:
    detector = engine.anomaly_detector
    found = []
    if blocked_level is not None:
        found.append(detector.detect_role_escalation_attempt(_SUBJECT, role, action, blocked_level))
    if activity is not None:
        if activity.exports_last_hour is not None:
            found.append(detector.detect_bulk_export(_SUBJECT, activity.exports_last_hour, level))
        if activity.access_hour is not None:
            found.append(detector.detect_off_hours_access(
                _SUBJECT, activity.access_hour, level,
                is_known_pattern=activity.usual_for_this_user,
            ))
        if activity.records_modified_last_minute is not None:
            found.append(detector.detect_rapid_modification(
                _SUBJECT, activity.records_modified_last_minute,
            ))
        if activity.failed_logins_last_5_min is not None:
            found.append(detector.detect_failed_auth_spike(
                _SUBJECT, activity.failed_logins_last_5_min,
            ))
    result = []
    for event in found:
        if event is None:
            continue
        item = event.to_dict()
        item.pop("user_id", None)
        item["description"] = item["description"].replace(f"User {_SUBJECT}", "This user").replace(
            f"user {_SUBJECT}", "this user")
        # Would block in an enforcing system; this preview blocks nothing.
        item["would_block"] = item.pop("auto_block")
        result.append(item)
    return result


@router.post("/preview")
async def governance_preview(
    body: GovernanceCheckRequest,
    user: dict = Depends(require_permission("approve_actions")),
):
    """Evaluate a proposed access against §21 rules; changes no permission."""
    role, action = Role(body.role), Action(body.action)
    engine = GovernanceEngine(company_id=user["company_id"])

    fields, levels, blocked = [], [], []
    for name in body.fields:
        level = classify_field(name)
        # check_permission, not engine.check_access: the latter reports an
        # "anomaly logged" that a preview never logs.
        allowed, reason = check_permission(role, action, level)
        levels.append(level)
        if not allowed:
            blocked.append(level)
        fields.append({
            "field": name,
            "data_level": DATA_LEVEL_LABELS[level],
            "data_level_rank": int(level),
            "allowed": allowed,
            "reason": reason,
        })

    highest = max(levels)
    blocked_level = max(blocked) if blocked else None
    anomalies = _anomalies(engine, role, action, body.activity, highest, blocked_level)

    authority = {
        act.value: DATA_LEVEL_LABELS[max_level]
        for act, max_level in AUTHORITY_MATRIX.get(role, {}).items()
    }
    retention = [
        {"data_type": data_type, **engine.get_retention_policy(data_type)}
        for data_type in body.data_types
        if data_type in RETENTION_SCHEDULE
    ]
    return advisory_response({
        "company_id": user["company_id"],
        "role": role.value,
        "action": action.value,
        "decision": "blocked" if blocked else "allowed",
        "highest_data_level": DATA_LEVEL_LABELS[highest],
        "fields": fields,
        "blocked_fields": len(blocked),
        "anomalies": anomalies,
        "role_authority": authority,
        "retention": retention,
        "permissions_changed": False,
        "stored": False,
    }, source="user_supplied")
