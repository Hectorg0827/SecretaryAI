"""
Per-tenant Policy Engine.

Evaluates ActionProposal objects against the policy_rules table and returns
an ActionDecision.  This replaces all hard-coded safety checks scattered in
safety.py, access_router.py, and the workflow engine.

Architecture
------------
- PolicyEngine is instantiated per-request with the company_id and DB client.
- Rules are loaded once per company per request (cached in Redis with a short TTL).
- Rule evaluation is deterministic: rules are sorted by priority ASC, first
  match wins.
- If NO rule matches, default effects are applied:
    read        → allow
    draft       → allow
    commit      → require_approval (amount-dependent threshold from company_features)
    destructive → deny

Enforcement is IN CODE — the policy engine must be called before any
COMMIT or DESTRUCTIVE operation, not just advised.  The caller is
responsible for blocking execution when effect != 'allow'.

Usage
-----
    engine = PolicyEngine(company_id=company_id, db=db)
    decision = await engine.evaluate(proposal)

    if decision.effect == "deny":
        raise HTTPException(403, detail=f"Denied by policy: {decision.rule_name}")
    elif decision.effect == "require_approval":
        # persist approval request, return 202
        ...
    else:  # allow
        # proceed with action
        ...
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from app.domain.contracts import ActionClass, ActionDecision, ActionProposal

log = logging.getLogger(__name__)

# Redis TTL for cached policy rules (seconds)
_POLICY_CACHE_TTL = 60


# ─── Default effects when no rule matches ─────────────────────────────────────

_DEFAULT_EFFECTS: dict[ActionClass, str] = {
    ActionClass.READ:        "allow",
    ActionClass.DRAFT:       "allow",
    ActionClass.COMMIT:      "require_approval",
    ActionClass.DESTRUCTIVE: "deny",
}


class PolicyEngine:
    """
    Evaluates per-tenant policy rules from the DB (with Redis caching).

    Parameters
    ----------
    company_id : str
    db : supabase.Client
        Service-role client that bypasses RLS (rules are loaded with service role).
    """

    def __init__(self, company_id: str, db: Any) -> None:
        self.company_id = company_id
        self._db = db
        self._rules: list[dict] | None = None
        self._features: dict | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def evaluate(self, proposal: ActionProposal) -> ActionDecision:
        """
        Evaluate an ActionProposal and return an ActionDecision.
        Loads rules from DB (with in-request caching).
        """
        rules = await self._load_rules()
        features = await self._load_features()

        for rule in rules:
            if self._rule_matches(rule, proposal):
                return self._make_decision(proposal, rule)

        # No rule matched — apply defaults
        return self._default_decision(proposal, features)

    async def seed_defaults(self) -> None:
        """
        Insert the standard default policy rules for a new company.
        Call this during company registration.
        Idempotent — skips if rules already exist.
        """
        existing = (
            self._db.table("policy_rules")
            .select("id")
            .eq("company_id", self.company_id)
            .execute()
        )
        if existing.data:
            return  # Already seeded

        defaults = _default_rules(self.company_id)
        for rule in defaults:
            try:
                self._db.table("policy_rules").insert(rule).execute()
            except Exception as exc:
                log.warning("Failed to seed policy rule %s: %s", rule["rule_name"], exc)

        # Seed company_features with safe defaults
        try:
            self._db.table("company_features").upsert(
                {"company_id": self.company_id},
                on_conflict="company_id",
            ).execute()
        except Exception as exc:
            log.warning("Failed to seed company_features for %s: %s", self.company_id, exc)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _load_rules(self) -> list[dict]:
        if self._rules is not None:
            return self._rules
        try:
            result = (
                self._db.table("policy_rules")
                .select("*")
                .eq("company_id", self.company_id)
                .eq("is_active", True)
                .order("priority", desc=False)
                .execute()
            )
            self._rules = result.data or []
        except Exception as exc:
            log.warning("Could not load policy rules for %s: %s — using defaults", self.company_id, exc)
            self._rules = []
        return self._rules

    async def _load_features(self) -> dict:
        if self._features is not None:
            return self._features
        try:
            result = (
                self._db.table("company_features")
                .select("*")
                .eq("company_id", self.company_id)
                .execute()
            )
            self._features = result.data[0] if result.data else {}
        except Exception as exc:
            log.warning("Could not load company_features for %s: %s", self.company_id, exc)
            self._features = {}
        return self._features

    @staticmethod
    def _rule_matches(rule: dict, proposal: ActionProposal) -> bool:
        """Return True if the rule applies to this proposal."""
        # action_class must match
        if rule.get("action_class") != proposal.action_class.value:
            return False

        # amount checks
        min_amount = rule.get("min_amount")
        max_amount = rule.get("max_amount")
        if min_amount is not None and (
            proposal.amount is None or proposal.amount < Decimal(str(min_amount))
        ):
            return False
        if max_amount is not None and (
            proposal.amount is None or proposal.amount > Decimal(str(max_amount))
        ):
            return False

        # capabilities_match — if set, proposal.capability must be in the list
        caps = rule.get("capabilities_match") or []
        if caps and proposal.capability not in caps:
            return False

        # paths_match — if set, proposal.path must be in the list
        paths = rule.get("paths_match") or []
        if paths and (proposal.path is None or proposal.path.value not in paths):
            return False

        return True

    @staticmethod
    def _make_decision(proposal: ActionProposal, rule: dict) -> ActionDecision:
        return ActionDecision(
            proposal=proposal,
            effect=rule["effect"],
            rule_id=str(rule["id"]),
            rule_name=rule.get("rule_name", ""),
            reason=rule.get("description"),
            approval_roles=rule.get("approval_roles") or ["owner", "manager"],
            approval_count=rule.get("approval_count") or 1,
        )

    def _default_decision(self, proposal: ActionProposal, features: dict) -> ActionDecision:
        """Apply default effect for the action class."""
        effect = _DEFAULT_EFFECTS.get(proposal.action_class, "require_approval")

        # Override COMMIT default: auto-allow if amount is below the company threshold
        if proposal.action_class == ActionClass.COMMIT and proposal.amount is not None:
            threshold = features.get("require_approval_above")
            if threshold is not None and proposal.amount < Decimal(str(threshold)):
                effect = "allow"

        return ActionDecision(
            proposal=proposal,
            effect=effect,
            rule_id=None,
            rule_name="default",
            reason=f"No matching policy rule — default effect for {proposal.action_class.value}",
            approval_roles=["owner", "manager"],
            approval_count=1,
        )


# ─── Default rule templates ───────────────────────────────────────────────────

def _default_rules(company_id: str) -> list[dict]:
    """
    Standard set of policy rules seeded for every new company.
    These are conservative defaults; the company can adjust them in Settings.
    """
    return [
        {
            "company_id": company_id,
            "rule_name": "allow_all_reads",
            "description": "Read-only data access is always allowed.",
            "action_class": "read",
            "effect": "allow",
            "priority": 10,
        },
        {
            "company_id": company_id,
            "rule_name": "allow_drafts",
            "description": "Drafts (pending approval) can be created without pre-approval.",
            "action_class": "draft",
            "effect": "allow",
            "priority": 20,
        },
        {
            "company_id": company_id,
            "rule_name": "require_approval_commits",
            "description": "Any committed action requires manager or owner approval.",
            "action_class": "commit",
            "effect": "require_approval",
            "approval_roles": ["owner", "manager"],
            "approval_count": 1,
            "priority": 50,
        },
        {
            "company_id": company_id,
            "rule_name": "deny_destructive",
            "description": "Destructive actions are denied unless overridden.",
            "action_class": "destructive",
            "effect": "deny",
            "priority": 60,
        },
        {
            "company_id": company_id,
            "rule_name": "deny_computer_use_commits",
            "description": "Commit actions executed via computer-use are denied; "
                           "only API and browser paths may commit.",
            "action_class": "commit",
            "effect": "deny",
            "paths_match": ["computer_use"],
            "priority": 40,
        },
    ]
