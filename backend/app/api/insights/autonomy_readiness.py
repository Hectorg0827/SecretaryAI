"""§20 progressive autonomy readiness — read-only, from the approval history.

For each kind of AI-proposed action this reports how reliable past proposals
were (approved as-is, approved after edits, rejected) and which autonomy tier
the action_schema engine would support. It is a recommendation only: nothing
here changes an autonomy or approval setting, approves a draft or executes an
action. The only source is the tenant's own ``drafts`` approval queue.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.insights._common import advisory_response, read_source
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.action_schema import (
    ActionImpact,
    ActionObject,
    ActionType,
    AutonomyEngine,
    Guardrails,
)

PREFIX = "/autonomy-readiness"
TAG = "insights: governance"

router = APIRouter()

WINDOW_DAYS = 365
MAX_ROWS = 5000
# Below these numbers of reviewed proposals the history is too thin to judge.
MIN_DECISIONS = 10
MIN_DECISIONS_TIER3 = 20
MIN_DECISIONS_TIER4 = 50
MIN_RELIABILITY_TIER4 = 0.90
# Fewer than half accepted (as-is or edited) → keep the AI to information only.
MIN_ACCEPTED_FOR_DRAFTS = 0.50

TIER_LABELS = {
    1: "Inform only",
    2: "Draft for approval",
    3: "Act within limits",
    4: "Autonomous",
}

# Draft statuses written by ApprovalQueue; anything else is a bad row.
_DECIDED = {"approved", "edited_and_approved", "rejected"}
_KNOWN_STATUSES = _DECIDED | {"pending", "expired"}

# Draft action types the §20 engine has guardrails for. Unlisted types keep
# human approval: the engine has no limits defined for them.
_ENGINE_TYPES: dict[str, tuple[ActionType, str]] = {
    "draft_purchase_order": (ActionType.REORDER, "Purchase orders"),
    "purchase_order": (ActionType.REORDER, "Purchase orders"),
    "draft_customer_email": (ActionType.CUSTOMER_OUTREACH, "Customer emails"),
    "email_reply": (ActionType.CUSTOMER_OUTREACH, "Email replies"),
}

_LABELS = {
    "draft_rep_assignment": "Sales rep assignments",
    "generate_external_report": "External reports",
    "cu_submit_form": "Form submissions in other apps",
    "cu_download_file": "File downloads in other apps",
    "cu_data_entry": "Data entry in other apps",
    "cu_financial_app_action": "Actions in financial apps",
}


def _label(action_type: str) -> str:
    if action_type in _ENGINE_TYPES:
        return _ENGINE_TYPES[action_type][1]
    return _LABELS.get(action_type, action_type.replace("_", " ").capitalize())


def _wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    """Conservative (95%) lower bound of a success rate; small samples score low."""
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denom)


def _amount(content: Any) -> Optional[float]:
    """Monetary amount recorded on a draft, if any (same keys as actions.py)."""
    if not isinstance(content, dict):
        return None
    for key in ("amount", "total", "total_amount", "value"):
        value = content.get(key)
        if value is None:
            continue
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return float(amount) if amount.is_finite() else None
    return None


def _engine_tier(action_type: ActionType, reliability: float,
                 largest_amount: Optional[float]) -> tuple[int, str]:
    """Highest tier at which the §20 engine would act on a typical proposal.

    The representative action carries the conservative reliability score as
    its confidence and the largest amount seen, so every past proposal would
    have fitted the guardrails. Evaluating it changes nothing.
    """
    guardrails = Guardrails()
    expiration = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    # Unknown amount → cannot prove it fits the automatic-order limit.
    cost = largest_amount if largest_amount is not None else math.inf
    best, reason = 2, ""
    for tier in (3, 4):
        action = ActionObject(
            type=action_type, title="Typical past proposal", description="",
            confidence=reliability, risk="medium",
            impact=ActionImpact(cost_delta=cost), expiration=expiration,
        )
        verdict = AutonomyEngine(tier=tier, guardrails=guardrails).evaluate(action)
        if verdict["disposition"] != "execute":
            if reliability < 0.65:
                reason = "Reliability is below the 65% the autonomy engine requires to act."
            elif action_type == ActionType.REORDER and largest_amount is None:
                reason = ("Past proposals don't record an amount, so they can't be checked "
                          "against the automatic-order limit.")
            else:
                reason = "Past proposals fall outside the automatic-action limits."
            break
        best = tier
    return best, reason


def _assess(action_type: str, counts: dict[str, int],
            largest_amount: Optional[float]) -> dict[str, Any]:
    approved = counts["approved"]
    edited = counts["edited_and_approved"]
    rejected = counts["rejected"]
    decided = approved + edited + rejected
    row: dict[str, Any] = {
        "action_type": action_type,
        "label": _label(action_type),
        "proposals": sum(counts.values()),
        "decided": decided,
        "approved_as_is": approved,
        "approved_with_edits": edited,
        "rejected": rejected,
        "pending": counts["pending"],
        "expired_unreviewed": counts["expired"],
        "as_is_rate": round(approved / decided, 4) if decided else None,
        "accepted_rate": round((approved + edited) / decided, 4) if decided else None,
        "rejected_rate": round(rejected / decided, 4) if decided else None,
        "reliability_score": None,
        # Everything in the approval queue currently waits for a person.
        "current_tier": 2,
        "current_level": TIER_LABELS[2],
        "recommended_tier": None,
        "recommended_level": None,
    }
    if decided < MIN_DECISIONS:
        row.update(
            assessment="insufficient_history",
            reason=(f"Only {decided} reviewed proposal(s); at least {MIN_DECISIONS} "
                    "are needed before judging reliability."),
        )
        return row

    reliability = _wilson_lower_bound(approved, decided)
    row["reliability_score"] = round(reliability, 4)
    if (approved + edited) / decided < MIN_ACCEPTED_FOR_DRAFTS:
        tier, assessment = 1, "inform_only"
        reason = "Most proposals of this kind were rejected; show them as information only."
    elif action_type not in _ENGINE_TYPES:
        tier, assessment = 2, "keep_approval"
        reason = "No automatic-action limits are defined for this kind of action; keep approving each one."
    else:
        tier, reason = _engine_tier(_ENGINE_TYPES[action_type][0], reliability, largest_amount)
        if tier >= 3 and decided < MIN_DECISIONS_TIER3:
            tier = 2
            reason = (f"Reliable so far, but {MIN_DECISIONS_TIER3} reviewed proposals are needed "
                      "before acting within limits.")
        if tier == 4 and (decided < MIN_DECISIONS_TIER4 or reliability < MIN_RELIABILITY_TIER4):
            tier = 3
            reason = (f"Full autonomy needs {MIN_DECISIONS_TIER4}+ reviewed proposals and a "
                      f"{MIN_RELIABILITY_TIER4:.0%} reliability score; acting within limits is supported.")
        assessment = "ready" if tier >= 3 else "keep_approval"
        if not reason:
            reason = "Past proposals were reliable and within the automatic-action limits."
    row.update(recommended_tier=tier, recommended_level=TIER_LABELS[tier],
               assessment=assessment, reason=reason)
    return row


def analyse_history(rows: list[Any]) -> dict[str, Any]:
    """Group AI drafts by action type; malformed rows are skipped and counted."""
    counts: dict[str, dict[str, int]] = {}
    largest: dict[str, float] = {}
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        action_type, status = row.get("action_type"), row.get("status")
        if (not isinstance(action_type, str) or not action_type.strip()
                or len(action_type) > 100 or status not in _KNOWN_STATUSES):
            skipped += 1
            continue
        bucket = counts.setdefault(action_type, {s: 0 for s in _KNOWN_STATUSES})
        bucket[status] += 1
        amount = _amount(row.get("content"))
        if amount is not None:
            largest[action_type] = max(largest.get(action_type, 0.0), abs(amount))

    action_types = sorted(
        (_assess(t, c, largest.get(t)) for t, c in counts.items()),
        key=lambda r: (-r["decided"], r["action_type"]),
    )
    total_decided = sum(r["decided"] for r in action_types)
    return {
        "action_types": action_types,
        "rows_read": len(rows),
        "rows_skipped": skipped,
        "total_decided": total_decided,
        "insufficient_history": not any(
            r["assessment"] != "insufficient_history" for r in action_types
        ),
        "ready_count": sum(r["assessment"] == "ready" for r in action_types),
    }


async def _read_ai_drafts(db, company_id: str, since: str) -> list[Any]:
    result = (
        db.table("drafts")
        .select("action_type, status, content, created_at")
        .eq("company_id", company_id)
        .eq("created_by", "ai")
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(MAX_ROWS)
        .execute()
    )
    rows = result.data
    if not isinstance(rows, list):
        raise RuntimeError("Approval history returned no rows list")
    return rows


@router.get("")
async def autonomy_readiness(
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    """Recommend an autonomy tier per action type; never changes any setting."""
    company_id = user["company_id"]
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = await read_source(
        _read_ai_drafts(db, company_id, since), source_name="Approval history",
    )
    result = analyse_history(rows)
    return advisory_response({
        **result,
        "company_id": company_id,
        "window_days": WINDOW_DAYS,
        "truncated": len(rows) >= MAX_ROWS,
        "min_decisions": MIN_DECISIONS,
        "tiers": {str(k): v for k, v in TIER_LABELS.items()},
        "settings_changed": False,
    }, source="connected_data")
