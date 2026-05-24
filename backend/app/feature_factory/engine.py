"""
SecretaryAI · Feature Factory — Tier 1 Rule Engine (Layers 5 & 6)
=================================================================
Executes a FeatureSpec. The spec is pure DATA; this engine is the only thing
that turns data into effects, and it does so with trusted handlers. There is
no eval(), no exec(), no dynamic code anywhere. That is what makes Tier 1 the
safe foundation.

THREE MODES (Layer 6 — progressive trust):
  * dry_run : read data, compute what WOULD happen, change nothing. For the
              "here's what I'd do, look right?" preview.
  * observe : same as dry_run but runs on the real schedule and is logged as a
              real run. A feature must succeed in observe N times before a
              human promotes it to act.
  * act     : actually perform effects — EXCEPT commit/destructive actions,
              which are turned into pending approvals instead of being done
              automatically. (Layer 5 — outcomes, not blind execution.)

BUSINESS DATA SEAM
  The engine reads/writes business data only through two injected protocols,
  both tenant-scoped by the caller. We do not assume your invoices/customers
  table shapes here; your developer implements these against the real schema.
"""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from . import capabilities as caps
from .contracts import (
    Action,
    ActionClass,
    Condition,
    FeatureSpec,
    PlannedAction,
    RunMode,
)


# ---------------------------------------------------------------------------
# Seams the host app implements against its real business tables.
# ---------------------------------------------------------------------------
class BusinessDataGateway(Protocol):
    async def fetch_records(
        self, entity: str, source_filter: dict[str, Any], tenant_id: UUID
    ) -> list[dict[str, Any]]:
        """Return tenant-scoped records for the given entity. READ ONLY."""
        ...


class EffectExecutor(Protocol):
    async def perform(
        self, action_type: str, params: dict[str, Any], record: dict[str, Any], tenant_id: UUID
    ) -> dict[str, Any]:
        """Actually perform a non-approval effect (e.g. add a flag). Returns result."""
        ...


# ---------------------------------------------------------------------------
# Pure condition evaluation — no side effects, fully unit-testable.
# ---------------------------------------------------------------------------
def _evaluate(condition: Condition, record: dict[str, Any]) -> bool:
    actual = record.get(condition.field)
    op, expected = condition.op, condition.value

    if actual is None and op not in ("==", "!="):
        return False

    try:
        if op == "<":   return actual < expected
        if op == "<=":  return actual <= expected
        if op == "==":  return actual == expected
        if op == "!=":  return actual != expected
        if op == ">=":  return actual >= expected
        if op == ">":   return actual > expected
        if op == "contains": return expected in (actual or "")
        if op == "in":  return actual in expected
        if op == "older_than_days":
            # 'actual' expected to be days-since already computed upstream, or
            # the gateway can pre-compute. Kept simple + explicit on purpose.
            return isinstance(actual, (int, float)) and actual >= expected
    except TypeError:
        return False
    return False


def _matches(spec: FeatureSpec, record: dict[str, Any]) -> bool:
    # All conditions must hold (AND). Keeping it AND-only at Tier 1 is a
    # deliberate simplicity/safety choice; OR/grouping is a later enhancement.
    return all(_evaluate(c, record) for c in spec.conditions)


def _human_summary(action: Action, record: dict[str, Any]) -> str:
    ref = record.get("ref") or record.get("id") or "record"
    if action.type == "flag":
        return f"Would flag {ref} for your attention"
    if action.type == "notify":
        return f"Would send an alert about {ref}"
    if action.type == "create_task":
        return f"Would create a follow-up task for {ref}"
    if action.type == "draft_email":
        return f"Would prepare (not send) an email about {ref}"
    if action.type == "summarize":
        return f"Would include {ref} in a summary"
    if action.type == "tag":
        return f"Would tag {ref}"
    return f"Would act on {ref}"


def _target_ref(record: dict[str, Any]) -> str:
    return str(record.get("ref") or record.get("id") or "unknown")


class RuleEngine:
    def __init__(self, data: BusinessDataGateway, effects: EffectExecutor, tenant_id: UUID):
        self._data = data
        self._effects = effects
        self._tenant_id = tenant_id

    async def run(self, spec: FeatureSpec, mode: RunMode) -> dict[str, list[PlannedAction]]:
        """
        Returns {"planned": [...], "executed": [...]}.
        In dry_run/observe, "executed" is always empty.
        In act, safe (read/draft) actions execute; commit/destructive actions
        are returned as planned-with-requires_approval and executed by the
        service layer only after human sign-off.
        """
        records = await self._data.fetch_records(
            spec.source_entity, spec.source_filter, self._tenant_id
        )

        planned: list[PlannedAction] = []
        executed: list[PlannedAction] = []

        for record in records:
            if not _matches(spec, record):
                continue
            for action in spec.actions:
                cap_key = caps.ACTION_TYPE_TO_CAPABILITY[action.type]
                action_class = caps.action_class_for(cap_key)
                needs_approval = caps.requires_approval(cap_key)

                pa = PlannedAction(
                    action_type=action.type,
                    action_class=action_class,
                    target_ref=_target_ref(record),
                    human_summary=_human_summary(action, record),
                    params=action.params,
                    requires_approval=needs_approval,
                )
                planned.append(pa)

                # Only ACT mode performs anything, and only the safe classes.
                if mode == RunMode.ACT and not needs_approval:
                    if action_class in (ActionClass.READ, ActionClass.DRAFT):
                        await self._effects.perform(
                            action.type, action.params, record, self._tenant_id
                        )
                        executed.append(pa)
                # commit/destructive in ACT mode -> left in 'planned' with
                # requires_approval=True; the service creates ff_approvals rows.

        return {"planned": planned, "executed": executed}
