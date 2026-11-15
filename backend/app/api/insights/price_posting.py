"""Read-only, jurisdiction-driven price-posting previews.

DRAFT ONLY: no agency export, no regulatory submission, no price publication.
Rules are versioned documentary candidates. Human verification must be recorded
per tenant before a rule can be considered reviewed.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_db
from app.api.insights._pricing_access import get_pricing_user
from app.api.compliance_integrity import _audit_blocked_write
from app.domain.contracts import ActionClass, ActionProposal, DataPath
from app.domain.policy import PolicyEngine
from app.intelligence.compliance.price_posting_preview import compile_price_posting_preview

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/price-posting"
TAG = "Insights: price posting (review only)"

RULE_FIELDS = (
    "id,program_key,version,country_code,region_code,program_name,"
    "required_details,allowed_channels,product_types,authority_url,"
    "authority_title,official_template_url,applicability_note,filing_deadline_rule"
)
BOOK_FIELDS = (
    "id,company_id,sku,package_label,market_code,channel,customer_ref,currency,"
    "list_price_per_case,effective_from,effective_to,revision,status,"
    "cost_snapshot_id,evidence_ref,approved_by,approved_at"
)


class PricePostingPreviewRequest(BaseModel):
    program_key: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z][A-Z0-9_]+$")
    price_book_ids: list[UUID] = Field(min_length=1, max_length=100)
    effective_date: date
    # Metadata is supplied for DRAFT VALIDATION only; the regulatory review
    # must verify each field against an authoritative document.
    line_details: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _rows(result, label: str) -> list[dict]:
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise HTTPException(status_code=503, detail=f"{label} unavailable or invalid")
    return rows


def _latest_rule(db, program_key: str) -> dict:
    try:
        rows = _rows(
            db.table("compliance_price_posting_rule_versions")
            .select(RULE_FIELDS).eq("program_key", program_key)
            .order("version", desc=True).limit(2).execute(), "Rulebook",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Compliance price rulebook unavailable")
        raise HTTPException(status_code=503, detail="Rulebook unavailable") from exc
    if not rows:
        raise HTTPException(status_code=404, detail="Price posting program not configured")
    rule = rows[0]
    if not rule.get("id") or not rule.get("country_code") or not rule.get("region_code"):
        raise HTTPException(status_code=503, detail="Rulebook version invalid")
    return rule


def _human_reviewed(db, company_id: str, rule_id: str) -> bool:
    try:
        rows = _rows(
            db.table("compliance_price_posting_rule_reviews")
            .select("id,company_id,rule_id,reviewer_id,reviewed_at,evidence_ref,review_note")
            .eq("company_id", company_id).eq("rule_id", rule_id)
            .limit(2).execute(), "Human regulatory verification",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Human regulatory review source unavailable")
        raise HTTPException(status_code=503, detail="Human regulatory verification unavailable") from exc
    if len(rows) > 1:
        raise HTTPException(status_code=503, detail="Human regulatory verification ambiguous")
    if not rows:
        return False
    row = rows[0]
    if (str(row.get("company_id")) != company_id
            or str(row.get("rule_id")) != rule_id
            or not row.get("reviewer_id") or not row.get("reviewed_at")
            or len(str(row.get("evidence_ref") or "").strip()) < 10
            or len(str(row.get("review_note") or "").strip()) < 20):
        raise HTTPException(status_code=503, detail="Human regulatory verification invalid")
    return True


@router.get("/programs")
async def list_price_posting_programs(
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    """Show documentary source versions, clearly distinguishing tenant reviews."""
    company_id = str(user["company_id"])
    try:
        rows = _rows(
            db.table("compliance_price_posting_rule_versions")
            .select(RULE_FIELDS).order("program_key", desc=False)
            .limit(101).execute(), "Rulebook",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Rulebook unavailable") from exc
    if len(rows) > 100:
        raise HTTPException(status_code=503, detail="Rulebook paging required")
    latest = {}
    for rule in rows:
        key = rule.get("program_key")
        if not key or not isinstance(rule.get("version"), int):
            raise HTTPException(status_code=503, detail="Rulebook version invalid")
        if key not in latest or rule["version"] > latest[key]["version"]:
            latest[key] = rule
    items = []
    for rule in latest.values():
        checked = _human_reviewed(db, company_id, str(rule["id"]))
        items.append({
            **rule,
            "human_verified_for_tenant": checked,
            "submission_permitted": False,
            "regulatory_deadlines_confirmed": checked and rule.get("filing_deadline_rule") is not None,
        })
    return {
        "programs": items,
        "count": len(items),
        "notice": "Candidate documentation only; no rule is automatically verified.",
        "agency_submission_enabled": False,
    }


@router.post("/preview")
async def preview_price_posting(
    request: PricePostingPreviewRequest,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    """Compile an advisory filing outline from tenant-owned PRICE BOOK evidence."""
    company_id = str(user["company_id"])
    ids = [str(pk) for pk in request.price_book_ids]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate price-book IDs")
    if any(k not in ids for k in request.line_details):
        raise HTTPException(status_code=422, detail="Orphan price-book metadata")

    rule = _latest_rule(db, request.program_key)
    verified = _human_reviewed(db, company_id, str(rule["id"]))
    try:
        rows = _rows(
            db.table("pricing_price_book")
            .select(BOOK_FIELDS)
            .eq("company_id", company_id)
            .in_("id", ids).limit(len(ids) + 1).execute(), "Price-book evidence",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Source price-book unavailable for compliance preview")
        raise HTTPException(status_code=503, detail="Price-book evidence unavailable") from exc
    indexed = {str(row.get("id")): row for row in rows}
    if len(rows) != len(ids) or len(indexed) != len(ids) or any(i not in indexed for i in ids):
        # No indication which ID belongs to which tenant.
        raise HTTPException(status_code=404, detail="Requested price-book evidence not found")
    if any(str(indexed[i].get("company_id")) != company_id for i in ids):
        raise HTTPException(status_code=503, detail="Price-book tenant scope invalid")
    try:
        draft = compile_price_posting_preview(
            rule=rule,
            verified=verified,
            price_book_rows=[indexed[i] for i in ids],
            detail_by_book_id=request.line_details,
            requested_effective=request.effective_date,
        )
    except (TypeError, ValueError, KeyError) as exc:
        log.warning("Compliance price-posting draft source invalid")
        raise HTTPException(status_code=503, detail="Price-posting source cannot be validated") from exc
    draft["read_consistency"] = "separate_tenant_scoped_queries_not_atomic_snapshot"
    return draft


class RuleHumanReviewRequest(BaseModel):
    """Explicit owner/manager attestation, not approval to post or file prices."""
    rule_id: UUID
    version: int = Field(ge=1)
    evidence_ref: str = Field(min_length=10, max_length=1500)
    review_note: str = Field(min_length=30, max_length=4000)
    checked_current_official_source: bool
    checked_tenant_license_applicability: bool
    acknowledged_no_filing_authority: bool


@router.post("/programs/{program_key}/review", status_code=201)
async def record_rule_human_review(
    program_key: str,
    request: RuleHumanReviewRequest,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    """Record an authorized person's *documentary review* with atomic audit.

    Existing tenant COMMIT policy must explicitly allow this action. A policy
    requiring approval CANNOT be bypassed by the reviewer pressing the button.
    The caller cannot supply reviewer/tenant identities or bypass server checks.
    """
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Owner or manager review required")
    if not (
        request.checked_current_official_source is True
        and request.checked_tenant_license_applicability is True
        and request.acknowledged_no_filing_authority is True
    ):
        raise HTTPException(status_code=422, detail="Every source and scope attestation is required")

    company_id = str(user["company_id"])
    actor_id = str(user["sub"])
    rule = _latest_rule(db, program_key)
    if str(rule["id"]) != str(request.rule_id) or rule.get("version") != request.version:
        raise HTTPException(status_code=409, detail="Rule version changed; reload and review again")
    if _human_reviewed(db, company_id, str(request.rule_id)):
        raise HTTPException(status_code=409, detail="This rule version already has a recorded review")

    proposal = ActionProposal(
        action_type="compliance.price_rule_review_record",
        action_class=ActionClass.COMMIT,
        description="Record human review of a candidate regulatory source",
        company_id=company_id,
        requested_by=actor_id,
        capability="compliance.rule_review",
        path=DataPath.API,
        entity_type="compliance_price_posting_rule_versions",
        entity_id=str(request.rule_id),
        proposed_changes={"version": request.version, "review_attested": True},
    )
    try:
        decision = await PolicyEngine(company_id, db).evaluate(proposal)
    except Exception as exc:
        _audit_blocked_write(
            db, table="compliance_price_posting_rule_reviews",
            record={"id": str(request.rule_id), "company_id": company_id},
            actor=actor_id, effect="deny", rule_id=None,
        )
        raise HTTPException(status_code=503, detail="Review policy unavailable; nothing recorded") from exc
    if decision.effect != "allow":
        _audit_blocked_write(
            db, table="compliance_price_posting_rule_reviews",
            record={"id": str(request.rule_id), "company_id": company_id},
            actor=actor_id, effect=decision.effect, rule_id=decision.rule_id,
        )
        if decision.effect == "require_approval":
            raise HTTPException(
                status_code=409,
                detail="Tenant policy requires separate approval before this human review can be recorded",
            )
        raise HTTPException(status_code=403, detail="Tenant policy rejected rule review")

    try:
        result = db.rpc("record_price_posting_rule_review", {
            "p_company_id": company_id,
            "p_rule_id": str(request.rule_id),
            "p_rule_version": request.version,
            "p_reviewer_id": actor_id,
            "p_evidence_ref": request.evidence_ref.strip(),
            "p_review_note": request.review_note.strip(),
            "p_acknowledged": True,
            "p_policy_effect": "allow",
            "p_policy_rule_id": decision.rule_id,
        }).execute()
        receipt = getattr(result, "data", None)
        if (not isinstance(receipt, dict)
                or receipt.get("state") != "documentary_review_recorded"
                or str(receipt.get("rule_id")) != str(request.rule_id)
                or receipt.get("rule_version") != request.version
                or not receipt.get("review_id")
                or not receipt.get("audit_event_id")
                or receipt.get("submission_permitted") is not False):
            raise ValueError("Human review/audit receipt unconfirmed")
    except Exception as exc:
        log.error("Human rule review write unconfirmed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Review and audit were not confirmed") from exc

    return {
        "rule_id": str(request.rule_id),
        "version": request.version,
        "review_id": receipt["review_id"],
        "audit_event_id": receipt["audit_event_id"],
        "state": "documentary_review_recorded",
        "submission_permitted": False,
        "price_publication_permitted": False,
        "regulatory_deadlines_verified_by_this_action": False,
    }
