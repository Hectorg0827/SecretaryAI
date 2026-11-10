"""
Lola authorization: organisational hierarchy, record-level and attribute-level
access control, delegations — all enforced on the server.

Layers (each one can only NARROW what the previous one allowed):

1. Tenant isolation — the company_id comes from the verified token AND must
   match the user's database row. Nothing in a request body can change it.
2. RBAC — the user's *current* database role (never the role cached in the
   JWT) selects the base permission set from ``app.auth.rbac.ROLE_PERMISSIONS``.
3. Org profile (``lola_org_members``) — org level, department, manager,
   territories, clearances and action limit. A profile can never lift a user
   above their role: e.g. a sales_rep marked "executive" is clamped to employee.
4. Record level — customer accounts are visible by company / department /
   reporting tree / territory / direct assignment / active delegation.
5. Attribute level — confidential fields (balances, costs, margins) are
   redacted unless the caller has financial access.

Fail-closed rules:
* User row unreadable → ``AuthorizationUnavailable`` (HTTP 503). Never guess.
* User row missing, inactive, or in another company → ``AuthorizationDenied``.
* Org profile unreadable → least privilege (employee, no territories,
  no clearances, no delegations) and ``org_profile_available=False``.
* Account-assignment table unreadable → only company-wide principals see
  accounts; everyone else sees none.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from app.auth.rbac import ROLE_PERMISSIONS
from app.lola.contracts import (
    Department,
    LolaContext,
    OrgLevel,
    PermissionDecision,
    ResourceType,
    Sensitivity,
)

log = logging.getLogger(__name__)

# Data classes Lola can never hand out through a delegation.
NON_DELEGABLE: frozenset[ResourceType] = frozenset({
    ResourceType.HR, ResourceType.PAYROLL, ResourceType.MEMORY, ResourceType.ATTACHMENT,
})

# Highest org level each role may hold. Prevents escalation through a
# mis-configured (or maliciously edited) org profile row.
_MAX_ORG_LEVEL: dict[str, OrgLevel] = {
    "owner": OrgLevel.EXECUTIVE,
    "manager": OrgLevel.EXECUTIVE,
    "back_office": OrgLevel.EMPLOYEE,
    "sales_rep": OrgLevel.EMPLOYEE,
    "viewer": OrgLevel.EMPLOYEE,
}
_ORG_RANK = {OrgLevel.EMPLOYEE: 0, OrgLevel.DEPARTMENT_MANAGER: 1, OrgLevel.EXECUTIVE: 2}

# Fields Lola redacts from records unless the caller has financial access.
FINANCIAL_FIELDS: frozenset[str] = frozenset({
    "balance", "current_balance", "credit_limit", "total_ytd", "unit_cost",
    "cost", "purchase_cost", "avg_cost", "margin", "gross_margin", "landed_cost", "amount_due",
    "open_balance", "lifetime_value",
})

_FINANCIAL_RESOURCES: frozenset[ResourceType] = frozenset({
    ResourceType.RECEIVABLES, ResourceType.FINANCIALS, ResourceType.INVENTORY_COST,
})

MAX_HIERARCHY_DEPTH = 12


class AuthorizationUnavailable(RuntimeError):
    """The authoritative permission source could not be read. Fail closed."""


class AuthorizationDenied(PermissionError):
    """The caller is not (or no longer) an active member of this tenant."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _enum(cls, value, default):
    try:
        return cls(value)
    except (ValueError, TypeError):
        return default


def _rows(result) -> list[dict]:
    data = getattr(result, "data", None)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


# ─── Delegation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Delegation:
    id: str
    grantor_id: str
    resource_types: frozenset[ResourceType]
    account_ids: frozenset[str]
    expires_at: datetime


# ─── Principal ───────────────────────────────────────────────────────────────


@dataclass
class Principal:
    """Everything Lola knows about *who is asking*, rebuilt on every request."""

    company_id: str
    user_id: str
    role: str
    org_level: OrgLevel
    department: Department
    manager_user_id: Optional[str] = None
    territories: frozenset[str] = frozenset()
    reports: frozenset[str] = frozenset()               # transitive reporting tree
    department_members: frozenset[str] = frozenset()    # same-department users
    financial_clearance: bool = False
    hr_clearance: bool = False
    action_limit: Optional[Decimal] = None
    delegations: tuple[Delegation, ...] = ()
    org_profile_available: bool = True
    permissions: frozenset[str] = field(default_factory=frozenset)

    # ── convenience predicates ────────────────────────────────────────────
    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def is_executive(self) -> bool:
        return self.org_level == OrgLevel.EXECUTIVE

    @property
    def is_department_manager(self) -> bool:
        return self.org_level == OrgLevel.DEPARTMENT_MANAGER

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def company_wide(self) -> bool:
        """Sees every record of the tenant (subject to sensitivity rules)."""
        return self.is_executive and self.has("view_all")

    @property
    def has_financial_access(self) -> bool:
        if self.is_owner:
            return True
        if not self.has("view_financials") and not self.financial_clearance:
            return False
        return self.is_executive or self.department == Department.FINANCE or self.financial_clearance

    def to_context(self, *, conversation_id: str, request_id: str, channel: str) -> LolaContext:
        return LolaContext(
            company_id=self.company_id,
            user_id=self.user_id,
            role=self.role,
            org_level=self.org_level,
            department=self.department,
            territories=sorted(self.territories),
            reports=sorted(self.reports),
            financial_clearance=self.financial_clearance,
            hr_clearance=self.hr_clearance,
            action_limit=self.action_limit,
            active_delegation_ids=[d.id for d in self.delegations],
            conversation_id=conversation_id,
            channel=channel,
            request_id=request_id,
            org_profile_available=self.org_profile_available,
        )


def _clamp_org_level(role: str, requested: OrgLevel) -> OrgLevel:
    ceiling = _MAX_ORG_LEVEL.get(role, OrgLevel.EMPLOYEE)
    if role == "manager" and requested == OrgLevel.DEPARTMENT_MANAGER:
        return requested
    return requested if _ORG_RANK[requested] <= _ORG_RANK[ceiling] else ceiling


def _default_org_level(role: str) -> OrgLevel:
    # Backward compatible with the existing RBAC: owner and manager already
    # have company-wide "view_all". A manager is narrowed to a department only
    # when the company configures an org profile saying so.
    return OrgLevel.EXECUTIVE if role in ("owner", "manager") else OrgLevel.EMPLOYEE


def _reporting_tree(user_id: str, members: list[dict]) -> frozenset[str]:
    children: dict[str, list[str]] = {}
    for row in members:
        mgr = row.get("manager_user_id")
        uid = row.get("user_id")
        if mgr and uid and mgr != uid:
            children.setdefault(str(mgr), []).append(str(uid))
    seen: set[str] = set()
    frontier = [user_id]
    depth = 0
    while frontier and depth < MAX_HIERARCHY_DEPTH:
        nxt: list[str] = []
        for node in frontier:
            for child in children.get(node, []):
                if child != user_id and child not in seen:
                    seen.add(child)
                    nxt.append(child)
        frontier = nxt
        depth += 1
    return frozenset(seen)


def load_user_row(db, company_id: str, user_id: str) -> dict:
    """Fresh, tenant-scoped user row. Raises on missing/inactive/unreadable."""
    if not company_id or not user_id:
        raise AuthorizationDenied("Session lacks tenant identity")
    try:
        rows = _rows(
            db.table("users").select("id,company_id,role,is_active")
            .eq("id", user_id).eq("company_id", company_id).limit(2).execute()
        )
    except Exception as exc:
        raise AuthorizationUnavailable("User authorization source unavailable") from exc
    if len(rows) != 1:
        raise AuthorizationDenied("User is not a member of this company")
    row = rows[0]
    if str(row.get("company_id")) != str(company_id) or row.get("is_active") is not True:
        raise AuthorizationDenied("User is not an active member of this company")
    if row.get("role") not in ROLE_PERMISSIONS:
        raise AuthorizationDenied("User role is not recognised")
    return row


def _load_delegations(db, company_id: str, user_id: str, members_by_id: dict[str, dict]) -> tuple[Delegation, ...]:
    now = _now()
    rows = _rows(
        db.table("lola_delegations").select("*")
        .eq("company_id", company_id).eq("grantee_user_id", user_id)
        .is_("revoked_at", "null").execute()
    )
    out: list[Delegation] = []
    grantor_ids = {str(r.get("grantor_user_id")) for r in rows if r.get("grantor_user_id")}
    active_grantors: set[str] = set()
    if grantor_ids:
        users = _rows(
            db.table("users").select("id,company_id,is_active")
            .eq("company_id", company_id).in_("id", sorted(grantor_ids)).execute()
        )
        active_grantors = {
            str(u["id"]) for u in users
            if u.get("is_active") is True and str(u.get("company_id")) == str(company_id)
        }
    for r in rows:
        expires = _parse_ts(r.get("expires_at"))
        grantor = str(r.get("grantor_user_id") or "")
        if r.get("revoked_at") or expires is None or expires <= now:
            continue
        if not grantor or grantor == user_id or grantor not in active_grantors:
            continue
        types = frozenset(
            t for t in (_enum(ResourceType, v, None) for v in (r.get("resource_types") or []))
            if t is not None and t not in NON_DELEGABLE
        )
        if not types:
            continue
        out.append(Delegation(
            id=str(r.get("id")),
            grantor_id=grantor,
            resource_types=types,
            account_ids=frozenset(str(a) for a in (r.get("account_ids") or [])),
            expires_at=expires,
        ))
    return tuple(out)


def build_principal(db, company_id: str, user_id: str, *, include_delegations: bool = True) -> Principal:
    """
    Build the request principal from the database. The JWT supplies only the
    identity (sub + company_id); role, hierarchy and grants are re-read here so
    a demoted, deactivated or re-assigned user loses access immediately.
    """
    user = load_user_row(db, company_id, user_id)
    role = user["role"]
    permissions = frozenset(ROLE_PERMISSIONS.get(role, set()))

    org_ok = True
    members: list[dict] = []
    profile: dict = {}
    try:
        members = _rows(
            db.table("lola_org_members").select("*").eq("company_id", company_id).execute()
        )
        profile = next((m for m in members if str(m.get("user_id")) == str(user_id)), {})
    except Exception:
        log.warning("Lola org profile unavailable; applying least privilege")
        org_ok = False

    if not org_ok:
        level = OrgLevel.EXECUTIVE if role == "owner" else OrgLevel.EMPLOYEE
        return Principal(
            company_id=company_id, user_id=user_id, role=role, org_level=level,
            department=Department.EXECUTIVE if role == "owner" else Department.GENERAL,
            org_profile_available=False, permissions=permissions,
        )

    if profile:
        level = _clamp_org_level(role, _enum(OrgLevel, profile.get("org_level"), OrgLevel.EMPLOYEE))
    else:
        level = _default_org_level(role)
    if role == "owner":
        level = OrgLevel.EXECUTIVE
    department = _enum(
        Department, profile.get("department"),
        Department.EXECUTIVE if level == OrgLevel.EXECUTIVE else Department.GENERAL,
    )
    limit = None
    if profile.get("action_limit") is not None:
        try:
            limit = Decimal(str(profile["action_limit"]))
        except (InvalidOperation, ValueError):
            limit = Decimal(0)  # unreadable limit → no authority, not unlimited

    reports = _reporting_tree(str(user_id), members)
    dept_members = frozenset(
        str(m["user_id"]) for m in members
        if m.get("department") == department.value and m.get("user_id")
    ) if level == OrgLevel.DEPARTMENT_MANAGER and department != Department.GENERAL else frozenset()

    members_by_id = {str(m.get("user_id")): m for m in members}
    delegations: tuple[Delegation, ...] = ()
    if include_delegations:
        try:
            delegations = _load_delegations(db, company_id, str(user_id), members_by_id)
        except Exception:
            log.warning("Lola delegations unavailable; ignoring all delegations")
            delegations = ()

    return Principal(
        company_id=company_id,
        user_id=str(user_id),
        role=role,
        org_level=level,
        department=department,
        manager_user_id=str(profile["manager_user_id"]) if profile.get("manager_user_id") else None,
        territories=frozenset(str(t) for t in (profile.get("territories") or []) if t),
        reports=reports,
        department_members=dept_members,
        # Clearances are explicit grants; executives of a role without
        # view_financials still do not get financial access by default.
        financial_clearance=bool(profile.get("financial_clearance")),
        hr_clearance=bool(profile.get("hr_clearance")),
        action_limit=limit,
        delegations=delegations,
        org_profile_available=True,
        permissions=permissions,
    )


# ─── Account scope (record level) ────────────────────────────────────────────


@dataclass
class AccountScope:
    """Which customer accounts a principal may see."""

    company_wide: bool
    visible_ids: frozenset[str]          # accounts.qb_customer_id or accounts.id
    basis: dict[str, str]                # account key → why it is visible
    assignments_available: bool = True

    def allows(self, key: Optional[str]) -> bool:
        if self.company_wide:
            return True
        return bool(key) and str(key) in self.visible_ids


def _account_keys(row: dict) -> list[str]:
    return [str(k) for k in (row.get("qb_customer_id"), row.get("id")) if k]


class Authorizer:
    """Answers every 'may this principal do X?' question for one request."""

    def __init__(self, principal: Principal, db, *, _grantor_cache: Optional[dict] = None):
        self.p = principal
        self._db = db
        self._accounts: Optional[list[dict]] = None
        self._accounts_ok = True
        self._scope: Optional[AccountScope] = None
        self._grantor_cache = _grantor_cache if _grantor_cache is not None else {}

    # ── capability level ───────────────────────────────────────────────────

    def _base(self, resource: ResourceType, action: str) -> PermissionDecision:
        p = self.p
        basis = [f"role:{p.role}", f"org_level:{p.org_level.value}"]

        def allow(scope: str, ceiling: Sensitivity, reason: str, redacted: Iterable[str] = ()) -> PermissionDecision:
            return PermissionDecision(
                allowed=True, resource_type=resource, action=action, reason=reason,
                basis=basis, scope=scope, sensitivity_ceiling=ceiling,
                redacted_fields=sorted(redacted),
            )

        def deny(reason: str) -> PermissionDecision:
            return PermissionDecision(
                allowed=False, resource_type=resource, action=action, reason=reason,
                basis=basis, scope="none", sensitivity_ceiling=Sensitivity.PUBLIC,
            )

        redact = () if p.has_financial_access else FINANCIAL_FIELDS
        record_scope = "company" if p.company_wide else (
            "department" if p.is_department_manager else "own"
        )

        if resource == ResourceType.CUSTOMER_ACCOUNT:
            if p.has("view_all"):
                return allow(record_scope, Sensitivity.INTERNAL, "Account access by role", redact)
            if p.has("view_customers"):
                return allow("company", Sensitivity.INTERNAL, "Customer directory access by role", redact)
            if p.has("view_own_accounts"):
                return allow("own", Sensitivity.INTERNAL, "Assigned accounts only", redact)
            return deny("Your role does not include customer accounts")

        if resource == ResourceType.SALES:
            if p.has("view_all"):
                return allow(record_scope, Sensitivity.INTERNAL, "Sales access by role")
            if p.has("view_own_accounts"):
                return allow("own", Sensitivity.INTERNAL, "Sales for your assigned accounts only")
            return deny("Your role does not include sales figures")

        if resource in (ResourceType.RECEIVABLES, ResourceType.FINANCIALS, ResourceType.INVENTORY_COST):
            if p.has_financial_access:
                scope = "company" if (p.is_owner or p.company_wide or p.department == Department.FINANCE) else record_scope
                return allow(scope, Sensitivity.CONFIDENTIAL, "Financial access granted")
            return deny("Financial information is restricted to finance and leadership")

        if resource == ResourceType.INVENTORY:
            if p.has("view_inventory") or p.has("view_all"):
                return allow("company", Sensitivity.INTERNAL, "Inventory access by role", redact)
            return deny("Your role does not include inventory")

        if resource == ResourceType.PURCHASING:
            if p.has("draft_purchase_orders") or p.has("view_all"):
                return allow("company", Sensitivity.INTERNAL, "Purchasing access by role", redact)
            return deny("Your role does not include purchasing")

        if resource == ResourceType.HR:
            if p.is_owner or (p.hr_clearance and p.department == Department.HR):
                return allow("company", Sensitivity.RESTRICTED, "HR clearance")
            return deny("HR information is restricted")

        if resource == ResourceType.PAYROLL:
            if p.is_owner and p.hr_clearance:
                return allow("company", Sensitivity.RESTRICTED, "Owner with HR clearance")
            return deny("Payroll information is restricted")

        if resource == ResourceType.MEMORY:
            return allow("own", Sensitivity.INTERNAL, "Personal memory")

        if resource == ResourceType.ATTACHMENT:
            # The caller's own file. Any cross-check against business data is
            # authorised separately by the tool doing it.
            return allow("own", Sensitivity.INTERNAL, "Your own attached file")

        return deny("Unknown resource")

    def can(self, resource: ResourceType, action: str = "read") -> PermissionDecision:
        decision = self._base(resource, action)
        if decision.allowed or resource in NON_DELEGABLE:
            return decision
        # A delegation can grant a capability the grantee lacks — but never
        # more than the grantor holds right now.
        for d in self.p.delegations:
            if resource not in d.resource_types:
                continue
            grantor_auth = self._grantor(d.grantor_id)
            if grantor_auth is None:
                continue
            g = grantor_auth._base(resource, action)
            if g.allowed:
                return PermissionDecision(
                    allowed=True, resource_type=resource, action=action,
                    reason="Delegated access", basis=decision.basis + [f"delegation:{d.id}"],
                    scope="delegated",
                    sensitivity_ceiling=g.sensitivity_ceiling,
                    # A financial delegation is an explicit grant of financial
                    # data; any other delegation redacts what EITHER party
                    # may not see.
                    redacted_fields=sorted(
                        set(g.redacted_fields) if resource in _FINANCIAL_RESOURCES else
                        set(g.redacted_fields)
                        | (set() if self.p.has_financial_access else set(FINANCIAL_FIELDS))
                    ),
                )
        return decision

    def _grantor(self, grantor_id: str) -> Optional["Authorizer"]:
        if grantor_id in self._grantor_cache:
            return self._grantor_cache[grantor_id]
        try:
            gp = build_principal(self._db, self.p.company_id, grantor_id, include_delegations=False)
            auth: Optional[Authorizer] = Authorizer(gp, self._db, _grantor_cache=self._grantor_cache)
        except (AuthorizationDenied, AuthorizationUnavailable):
            auth = None
        self._grantor_cache[grantor_id] = auth
        return auth

    # ── record level ──────────────────────────────────────────────────────

    def account_rows(self) -> list[dict]:
        if self._accounts is None:
            try:
                self._accounts = _rows(
                    self._db.table("accounts")
                    .select("id,qb_customer_id,name,territory,assigned_rep_id")
                    .eq("company_id", self.p.company_id).execute()
                )
            except Exception:
                log.warning("Account assignments unavailable; restricting to company-wide principals")
                self._accounts = []
                self._accounts_ok = False
        return self._accounts

    def _own_visible(self, rows: list[dict]) -> dict[str, str]:
        p = self.p
        team = set(p.reports) | set(p.department_members)
        out: dict[str, str] = {}
        for row in rows:
            rep = str(row.get("assigned_rep_id") or "")
            terr = str(row.get("territory") or "")
            why = None
            if rep and rep == p.user_id:
                why = "assigned_to_you"
            elif rep and rep in team:
                why = "reporting_line" if rep in p.reports else "department"
            elif terr and terr in p.territories:
                why = f"territory:{terr}"
            if why:
                for k in _account_keys(row):
                    out[k] = why
        return out

    def account_scope(self) -> AccountScope:
        if self._scope is not None:
            return self._scope
        decision = self._base(ResourceType.CUSTOMER_ACCOUNT, "read")
        rows = self.account_rows()
        if decision.allowed and decision.scope == "company":
            self._scope = AccountScope(True, frozenset(), {}, self._accounts_ok)
            return self._scope
        visible: dict[str, str] = {}
        if decision.allowed and self._accounts_ok:
            visible.update(self._own_visible(rows))
        # Delegated accounts (bounded by the grantor's own current visibility).
        if self._accounts_ok:
            for d in self.p.delegations:
                if ResourceType.CUSTOMER_ACCOUNT not in d.resource_types:
                    continue
                g = self._grantor(d.grantor_id)
                if g is None:
                    continue
                g_scope = g.account_scope()
                for row in rows:
                    keys = _account_keys(row)
                    explicitly = bool(d.account_ids & set(keys))
                    assigned_to_grantor = str(row.get("assigned_rep_id") or "") == d.grantor_id
                    if (explicitly or (not d.account_ids and assigned_to_grantor)) and any(
                        g_scope.allows(k) for k in keys
                    ):
                        for k in keys:
                            visible.setdefault(k, f"delegation:{d.id}")
        self._scope = AccountScope(False, frozenset(visible), visible, self._accounts_ok)
        return self._scope

    def can_see_account(self, key: Optional[str]) -> bool:
        return self.account_scope().allows(key)

    # ── attribute level ──────────────────────────────────────────────────

    def redact(self, record: dict, decision: PermissionDecision) -> dict:
        if not decision.redacted_fields:
            return dict(record)
        hidden = set(decision.redacted_fields)
        return {k: v for k, v in record.items() if k not in hidden}

    def sensitivity_ceiling(self) -> Sensitivity:
        if self.can(ResourceType.HR).allowed:
            return Sensitivity.RESTRICTED
        if self.p.has_financial_access:
            return Sensitivity.CONFIDENTIAL
        return Sensitivity.INTERNAL
