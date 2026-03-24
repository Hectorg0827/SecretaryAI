"""
§21 — Governance, Security & Access Control.

Data classification, authority matrix, anomaly detection in system behavior,
and data retention schedules for wholesale distribution intelligence.

Data Classification Levels
───────────────────────────
  L1 — Public        : marketing materials, public pricing
  L2 — Internal      : operational data (inventory counts, order volumes)
  L3 — Confidential  : customer PII, contract pricing, margin data
  L4 — Restricted    : credentials, bank info, supplier agreements, M&A data

Authority Matrix (action × role)
──────────────────────────────────
  Actions:          view | draft | approve | execute | delete | export

  Roles (ascending authority):
    viewer           — L1, L2 view only
    analyst          — L1, L2, L3 view; draft actions
    manager          — L1–L3 view/approve; L4 view; execute Tier 1–3 actions
    executive        — L1–L4 view/approve; execute any action
    system_admin     — all permissions; manage users; configure integrations

Anomaly Detection
──────────────────
  Detects unusual system behavior that may indicate:
    - Data exfiltration (bulk exports at unusual hours)
    - Credential misuse (logins from new locations, concurrent sessions)
    - Unauthorized action escalation (lower-tier role attempting higher-tier action)
    - Data tampering (record modifications without matching approval)

Data Retention Schedule
────────────────────────
  Operational data (orders, invoices)     : 7 years (tax compliance)
  Customer PII                            : 5 years post last interaction
  Action audit log                        : 10 years (legal hold capability)
  Chat conversations                      : 2 years
  Report snapshots                        : 90 days (rolling)
  Session logs                            : 1 year
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ─── Data Classification ─────────────────────────────────────────────────────


class DataLevel(int, Enum):
    PUBLIC = 1
    INTERNAL = 2
    CONFIDENTIAL = 3
    RESTRICTED = 4


DATA_LEVEL_LABELS = {
    DataLevel.PUBLIC: "public",
    DataLevel.INTERNAL: "internal",
    DataLevel.CONFIDENTIAL: "confidential",
    DataLevel.RESTRICTED: "restricted",
}

# Field-level data classification for common distribution data
FIELD_CLASSIFICATION: dict[str, DataLevel] = {
    # L1 — Public
    "product_name": DataLevel.PUBLIC,
    "category": DataLevel.PUBLIC,
    "public_price": DataLevel.PUBLIC,
    # L2 — Internal
    "inventory_count": DataLevel.INTERNAL,
    "order_volume": DataLevel.INTERNAL,
    "sku": DataLevel.INTERNAL,
    "reorder_point": DataLevel.INTERNAL,
    "lead_time": DataLevel.INTERNAL,
    # L3 — Confidential
    "customer_name": DataLevel.CONFIDENTIAL,
    "customer_email": DataLevel.CONFIDENTIAL,
    "customer_phone": DataLevel.CONFIDENTIAL,
    "customer_address": DataLevel.CONFIDENTIAL,
    "contract_price": DataLevel.CONFIDENTIAL,
    "gross_margin": DataLevel.CONFIDENTIAL,
    "ar_balance": DataLevel.CONFIDENTIAL,
    "customer_credit_limit": DataLevel.CONFIDENTIAL,
    # L4 — Restricted
    "api_key": DataLevel.RESTRICTED,
    "oauth_token": DataLevel.RESTRICTED,
    "bank_account": DataLevel.RESTRICTED,
    "supplier_agreement": DataLevel.RESTRICTED,
    "employee_salary": DataLevel.RESTRICTED,
    "acquisition_target": DataLevel.RESTRICTED,
}


def classify_field(field_name: str) -> DataLevel:
    """Lookup data classification for a field name (case-insensitive partial match)."""
    fl = field_name.lower()
    for known_field, level in FIELD_CLASSIFICATION.items():
        if known_field in fl or fl in known_field:
            return level
    return DataLevel.INTERNAL  # default: treat unknown as internal


# ─── Authority Matrix ─────────────────────────────────────────────────────────


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    MANAGER = "manager"
    EXECUTIVE = "executive"
    SYSTEM_ADMIN = "system_admin"


class Action(str, Enum):
    VIEW = "view"
    DRAFT = "draft"
    APPROVE = "approve"
    EXECUTE = "execute"
    DELETE = "delete"
    EXPORT = "export"
    CONFIGURE = "configure"


# Authority matrix: (role, action, max_data_level) → allowed
# Expressed as: role → {action → max data level allowed}
AUTHORITY_MATRIX: dict[Role, dict[Action, DataLevel]] = {
    Role.VIEWER: {
        Action.VIEW: DataLevel.INTERNAL,
    },
    Role.ANALYST: {
        Action.VIEW: DataLevel.CONFIDENTIAL,
        Action.DRAFT: DataLevel.CONFIDENTIAL,
        Action.EXPORT: DataLevel.INTERNAL,
    },
    Role.MANAGER: {
        Action.VIEW: DataLevel.RESTRICTED,
        Action.DRAFT: DataLevel.CONFIDENTIAL,
        Action.APPROVE: DataLevel.CONFIDENTIAL,
        Action.EXECUTE: DataLevel.CONFIDENTIAL,
        Action.EXPORT: DataLevel.CONFIDENTIAL,
    },
    Role.EXECUTIVE: {
        Action.VIEW: DataLevel.RESTRICTED,
        Action.DRAFT: DataLevel.RESTRICTED,
        Action.APPROVE: DataLevel.RESTRICTED,
        Action.EXECUTE: DataLevel.RESTRICTED,
        Action.EXPORT: DataLevel.RESTRICTED,
        Action.DELETE: DataLevel.CONFIDENTIAL,
    },
    Role.SYSTEM_ADMIN: {
        Action.VIEW: DataLevel.RESTRICTED,
        Action.DRAFT: DataLevel.RESTRICTED,
        Action.APPROVE: DataLevel.RESTRICTED,
        Action.EXECUTE: DataLevel.RESTRICTED,
        Action.EXPORT: DataLevel.RESTRICTED,
        Action.DELETE: DataLevel.RESTRICTED,
        Action.CONFIGURE: DataLevel.RESTRICTED,
    },
}


def check_permission(
    role: Role,
    action: Action,
    data_level: DataLevel,
) -> tuple[bool, str]:
    """
    Check if a role can perform an action on data of a given classification.
    Returns (allowed, reason).
    """
    role_perms = AUTHORITY_MATRIX.get(role, {})
    max_level = role_perms.get(action)

    if max_level is None:
        return False, (
            f"Role '{role.value}' does not have permission to '{action.value}' any data."
        )

    if data_level > max_level:
        return False, (
            f"Role '{role.value}' can only '{action.value}' data up to level "
            f"'{DATA_LEVEL_LABELS[max_level]}' — "
            f"requested level is '{DATA_LEVEL_LABELS[data_level]}'."
        )

    return True, "Permitted."


# ─── Anomaly Detection ───────────────────────────────────────────────────────


class AnomalyType(str, Enum):
    BULK_EXPORT = "bulk_export"
    OFF_HOURS_ACCESS = "off_hours_access"
    NEW_LOCATION_LOGIN = "new_location_login"
    CONCURRENT_SESSIONS = "concurrent_sessions"
    ROLE_ESCALATION_ATTEMPT = "role_escalation_attempt"
    RAPID_RECORD_MODIFICATION = "rapid_record_modification"
    UNUSUAL_QUERY_VOLUME = "unusual_query_volume"
    FAILED_AUTH_SPIKE = "failed_auth_spike"


@dataclass
class AnomalyEvent:
    """A detected security anomaly."""
    anomaly_type: AnomalyType
    severity: str               # low | medium | high | critical
    user_id: str
    description: str
    context: dict
    recommended_action: str
    auto_block: bool = False    # should the system block the action immediately?

    def to_dict(self) -> dict:
        return {
            "anomaly_type": self.anomaly_type.value,
            "severity": self.severity,
            "user_id": self.user_id,
            "description": self.description,
            "context": self.context,
            "recommended_action": self.recommended_action,
            "auto_block": self.auto_block,
        }


class AnomalyDetector:
    """
    Detects unusual patterns in user and system behavior.
    Operates on event streams — call detect() per event.
    """

    EXPORT_THRESHOLD_PER_HOUR = 5           # >5 bulk exports/hour → anomaly
    MODIFICATION_THRESHOLD_PER_MINUTE = 30  # >30 record mods/minute → anomaly
    FAILED_AUTH_THRESHOLD = 10              # >10 failures in 5 min → anomaly
    BUSINESS_HOURS = (7, 20)                # 7am–8pm local time

    def detect_bulk_export(
        self, user_id: str, export_count_last_hour: int, data_level: DataLevel
    ) -> Optional[AnomalyEvent]:
        if export_count_last_hour < self.EXPORT_THRESHOLD_PER_HOUR:
            return None
        severity = "critical" if data_level >= DataLevel.CONFIDENTIAL else "medium"
        return AnomalyEvent(
            anomaly_type=AnomalyType.BULK_EXPORT,
            severity=severity,
            user_id=user_id,
            description=(
                f"User {user_id} performed {export_count_last_hour} bulk exports "
                f"in the last hour (data level: {DATA_LEVEL_LABELS[data_level]})"
            ),
            context={"export_count": export_count_last_hour, "data_level": data_level.value},
            recommended_action=(
                "Review exported data. If unauthorized, revoke session and audit access log."
            ),
            auto_block=data_level >= DataLevel.CONFIDENTIAL,
        )

    def detect_off_hours_access(
        self,
        user_id: str,
        access_hour: int,        # 0–23
        data_level: DataLevel,
        is_known_pattern: bool = False,
    ) -> Optional[AnomalyEvent]:
        in_business_hours = self.BUSINESS_HOURS[0] <= access_hour < self.BUSINESS_HOURS[1]
        if in_business_hours or is_known_pattern:
            return None
        severity = "high" if data_level >= DataLevel.CONFIDENTIAL else "low"
        return AnomalyEvent(
            anomaly_type=AnomalyType.OFF_HOURS_ACCESS,
            severity=severity,
            user_id=user_id,
            description=(
                f"User {user_id} accessed {DATA_LEVEL_LABELS[data_level]} data "
                f"at {access_hour:02d}:xx — outside normal business hours"
            ),
            context={"hour": access_hour, "data_level": data_level.value},
            recommended_action=(
                "Verify with user whether this was intentional. "
                "Alert manager if data level is Confidential or Restricted."
            ),
            auto_block=False,
        )

    def detect_role_escalation_attempt(
        self,
        user_id: str,
        role: Role,
        requested_action: Action,
        data_level: DataLevel,
    ) -> Optional[AnomalyEvent]:
        allowed, _ = check_permission(role, requested_action, data_level)
        if allowed:
            return None
        return AnomalyEvent(
            anomaly_type=AnomalyType.ROLE_ESCALATION_ATTEMPT,
            severity="high",
            user_id=user_id,
            description=(
                f"User {user_id} (role: {role.value}) attempted '{requested_action.value}' "
                f"on {DATA_LEVEL_LABELS[data_level]} data — blocked by authority matrix"
            ),
            context={
                "role": role.value,
                "action": requested_action.value,
                "data_level": data_level.value,
            },
            recommended_action=(
                "Deny request. If pattern repeats, review whether role assignment is appropriate."
            ),
            auto_block=True,
        )

    def detect_rapid_modification(
        self,
        user_id: str,
        modifications_last_minute: int,
    ) -> Optional[AnomalyEvent]:
        if modifications_last_minute < self.MODIFICATION_THRESHOLD_PER_MINUTE:
            return None
        return AnomalyEvent(
            anomaly_type=AnomalyType.RAPID_RECORD_MODIFICATION,
            severity="critical",
            user_id=user_id,
            description=(
                f"User {user_id} modified {modifications_last_minute} records "
                "in the last minute — possible automated script or data tampering"
            ),
            context={"modifications_per_minute": modifications_last_minute},
            recommended_action=(
                "Immediately pause session. Audit modified records. "
                "Verify with user and manager before restoring access."
            ),
            auto_block=True,
        )

    def detect_failed_auth_spike(
        self,
        user_id: str,
        failed_attempts_last_5_min: int,
    ) -> Optional[AnomalyEvent]:
        if failed_attempts_last_5_min < self.FAILED_AUTH_THRESHOLD:
            return None
        return AnomalyEvent(
            anomaly_type=AnomalyType.FAILED_AUTH_SPIKE,
            severity="high",
            user_id=user_id,
            description=(
                f"{failed_attempts_last_5_min} failed authentication attempts "
                f"for user {user_id} in the last 5 minutes"
            ),
            context={"failed_attempts": failed_attempts_last_5_min},
            recommended_action=(
                "Lock account for 30 minutes. Notify user via registered email."
            ),
            auto_block=True,
        )


# ─── Data Retention Schedule ─────────────────────────────────────────────────


RETENTION_SCHEDULE: dict[str, dict] = {
    "orders": {
        "retention_years": 7,
        "basis": "Tax compliance (IRS, state)",
        "auto_purge": False,
        "legal_hold_capable": True,
    },
    "invoices": {
        "retention_years": 7,
        "basis": "Tax compliance",
        "auto_purge": False,
        "legal_hold_capable": True,
    },
    "customer_pii": {
        "retention_years": 5,
        "basis": "5 years post last interaction (CCPA / GDPR-aligned)",
        "auto_purge": True,
        "legal_hold_capable": True,
    },
    "action_audit_log": {
        "retention_years": 10,
        "basis": "Legal hold / SOX / internal policy",
        "auto_purge": False,
        "legal_hold_capable": True,
    },
    "chat_conversations": {
        "retention_years": 2,
        "basis": "Operational reference",
        "auto_purge": True,
        "legal_hold_capable": False,
    },
    "report_snapshots": {
        "retention_days": 90,
        "basis": "Rolling operational cache",
        "auto_purge": True,
        "legal_hold_capable": False,
    },
    "session_logs": {
        "retention_years": 1,
        "basis": "Security audit",
        "auto_purge": True,
        "legal_hold_capable": False,
    },
    "supplier_agreements": {
        "retention_years": 10,
        "basis": "Contract law — active + 7 years post expiry",
        "auto_purge": False,
        "legal_hold_capable": True,
    },
}


# ─── Governance Engine ────────────────────────────────────────────────────────


class GovernanceEngine:
    """
    Unified governance layer — access control, anomaly detection,
    data classification, and retention policy checks.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id
        self.anomaly_detector = AnomalyDetector()

    def check_access(
        self,
        user_id: str,
        role: "Role | str",
        action: "Action | str",
        field_name: str,
    ) -> tuple[bool, str]:
        """Check if a user can perform action on a specific data field."""
        role = Role(role) if isinstance(role, str) else role
        action = Action(action) if isinstance(action, str) else action
        data_level = classify_field(field_name)
        allowed, reason = check_permission(role, action, data_level)

        # Also check for anomalous escalation
        if not allowed:
            anomaly = self.anomaly_detector.detect_role_escalation_attempt(
                user_id=user_id, role=role, requested_action=action, data_level=data_level
            )
            if anomaly:
                reason = f"{reason} [ANOMALY LOGGED: {anomaly.anomaly_type.value}]"

        return allowed, reason

    def filter_response(
        self,
        data: dict,
        role: "Role | str",
        action: "Action | str" = Action.VIEW,
    ) -> dict:
        """
        Strip fields from a response dict that the role cannot access.
        Returns a filtered dict safe to return to the user.
        """
        filtered: dict = {}
        role = Role(role) if isinstance(role, str) else role
        action = Action(action) if isinstance(action, str) else action
        for key, value in data.items():
            data_level = classify_field(key)
            allowed, _ = check_permission(role, action, data_level)
            if allowed:
                filtered[key] = value
            else:
                filtered[key] = "[REDACTED]"
        return filtered

    def filter_for_role(self, data: dict, role_str: str) -> dict:
        """Alias for filter_response that accepts a role string."""
        return self.filter_response(data, role_str)

    def get_retention_policy(self, data_type: str) -> dict:
        """Return the retention policy for a data type."""
        return RETENTION_SCHEDULE.get(data_type, {
            "retention_years": 7,
            "basis": "Default policy",
            "auto_purge": False,
            "legal_hold_capable": False,
        })
