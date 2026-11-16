"""Tenant-scoped internal price-posting case preparation and external references.

NO price publication, government filing, connector write, or agency acceptance
is implied by this module. Human-provided document links are UNVERIFIED.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.compliance_integrity import _audit_blocked_write
from app.api.deps import get_db
from app.api.insights._pricing_access import get_pricing_user
from app.api.insights.price_posting import _human_reviewed, _latest_rule, _rows
from app.domain.contracts import ActionClass, ActionProposal, DataPath
from app.domain.policy import PolicyEngine

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/price-preparations"
TAG = "Insights: internal price posting preparation"

CASE_FIELDS = (
    "id,company_id,rule_id,rule_version,created_by,effective_date,"
    "price_book_snapshot,source_fingerprint,state,created_at"
)
REFERENCE_FIELDS = (
    "id,company_id,preparation_id,created_by,reference_kind,"
    "external_reference,claimed_event_date,note,provenance_status,created_at"
)


class PrepareInternalCaseRequest(BaseModel):
    program_key: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z][A-Z0-9_]+$")
    rule_id: UUID
    version: int = Field(ge=1)
    effective_date: date
    price_book_ids: list[UUID] = Field(min_length=1, max_length=50)
    acknowledged_no_filing_authority: bool


class AddHumanExternalReferenceRequest(BaseModel):
    kind: Literal[
        "external_document_reference",
        "agency_message_reference",
        "rejection_reference",
    ]
    external_reference: str = Field(min_length=10, max_length=1500)
    claimed_event_date: date | None = None
    note: str = Field(min_length=30, max_length=4000)
    acknowledged_unverified: bool


def _require_owner_manager(user: dict) -> None:
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Owner or manager required")


async def _allow_audited_action(
    db, user: dict, *, action: str, entity_type: str, entity_id: str,
    changes: dict,
) -> str | None:
    company_id = str(user["company_id"])
    actor = str(user["sub"])
    proposal = ActionProposal(
        action_type=action,
        action_class=ActionClass.COMMIT,
        description="Record internal price-posting evidence, not a government filing",
        company_id=company_id,
        requested_by=actor,
        capability="compliance.price_preparation",
        path=DataPath.API,
        entity_type=entity_type,
        entity_id=entity_id,
        proposed_changes=changes,
    )
    try:
        decision = await PolicyEngine(company_id, db).evaluate(proposal)
    except Exception as exc:
        _audit_blocked_write(
            db, table=entity_type,
            record={"id": entity_id, "company_id": company_id},
            actor=actor, effect="deny", rule_id=None,
        )
        raise HTTPException(
            status_code=503,
            detail="Preparation policy unavailable; no record was created",
        ) from exc

    if decision.effect != "allow":
        _audit_blocked_write(
            db, table=entity_type,
            record={"id": entity_id, "company_id": company_id},
            actor=actor, effect=decision.effect, rule_id=decision.rule_id,
        )
        if decision.effect == "require_approval":
            raise HTTPException(status_code=409,
                detail="Tenant policy approval is required; no record was created")
        raise HTTPException(status_code=403,
            detail="Tenant policy blocked internal evidence recording")
    return decision.rule_id


def _read_case(db, company_id: str, case_id: UUID) -> dict:
    try:
        rows = _rows(
            db.table("compliance_price_posting_preparations").select(CASE_FIELDS)
            .eq("company_id", company_id).eq("id", str(case_id))
            .limit(2).execute(), "Internal price preparation",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Internal preparation source unavailable")
        raise HTTPException(status_code=503, detail="Preparation source unavailable") from exc
    if not rows:
        raise HTTPException(status_code=404, detail="Preparation not found")
    if len(rows) != 1 or str(rows[0].get("company_id")) != company_id:
        raise HTTPException(status_code=503, detail="Preparation source identity ambiguous")
    return rows[0]


@router.get("")
async def list_internal_preparations(
    response: Response,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    company_id = str(user["company_id"])
    try:
        rows = _rows(
            db.table("compliance_price_posting_preparations")
            .select(CASE_FIELDS).eq("company_id", company_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit).execute(),
            "Internal preparation ledger",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Internal preparation ledger unavailable")
        raise HTTPException(status_code=503, detail="Preparation ledger unavailable") from exc
    if any(str(item.get("company_id")) != company_id for item in rows):
        raise HTTPException(status_code=503, detail="Tenant scope invalid")
    if any(item.get("state") != "internal_preparation_only" for item in rows):
        raise HTTPException(status_code=503, detail="Unrecognized preparation state")
    return {
        "items": rows[:limit], "next_offset": offset+limit if len(rows)>limit else None,
        "submission_permitted": False, "agency_confirmation": False,
    }


@router.post("", status_code=201)
async def create_internal_preparation(
    request: PrepareInternalCaseRequest,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    _require_owner_manager(user)
    if request.acknowledged_no_filing_authority is not True:
        raise HTTPException(status_code=422, detail="No-filing acknowledgement required")
    ids = [str(x) for x in request.price_book_ids]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate source price-book IDs")

    company_id = str(user["company_id"])
    rule = _latest_rule(db, request.program_key)
    if str(rule["id"]) != str(request.rule_id) or rule.get("version") != request.version:
        raise HTTPException(status_code=409, detail="Rule version changed; reload first")
    if not _human_reviewed(db, company_id, str(request.rule_id)):
        raise HTTPException(status_code=409, detail="Human source review must be recorded first")

    policy_rule_id = await _allow_audited_action(
        db, user,
        action="compliance.price_preparation_record",
        entity_type="compliance_price_posting_preparations",
        entity_id=str(request.rule_id),
        changes={"rule_version": request.version, "price_book_count": len(ids)},
    )
    try:
        result = db.rpc("create_price_posting_preparation", {
            "p_company_id": company_id,
            "p_rule_id": str(request.rule_id),
            "p_rule_version": request.version,
            "p_actor_id": str(user["sub"]),
            "p_effective_date": request.effective_date.isoformat(),
            "p_book_ids": ids,
            "p_policy_effect": "allow",
            "p_policy_rule_id": policy_rule_id,
        }).execute()
        receipt = getattr(result, "data", None)
        if (not isinstance(receipt, dict)
            or receipt.get("state") != "internal_preparation_only"
            or receipt.get("submission_permitted") is not False
            or receipt.get("agency_confirmation") is not False
            or receipt.get("price_publication_permitted") is not False
            or receipt.get("source_count") != len(ids)
            or not receipt.get("preparation_id")
            or not receipt.get("audit_event_id")
            or len(str(receipt.get("source_fingerprint", ""))) != 32):
            raise ValueError("Internal preparation and audit receipt incomplete")
    except Exception as exc:
        log.error("Price preparation write unconfirmed error_type=%s",type(exc).__name__)
        raise HTTPException(status_code=503, detail="Preparation and audit not confirmed") from exc
    return receipt


@router.get("/{case_id}/references")
async def list_external_references(
    case_id: UUID,
    response: Response,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    company_id = str(user["company_id"])
    _read_case(db, company_id, case_id)
    try:
        rows = _rows(
            db.table("compliance_price_posting_external_references")
            .select(REFERENCE_FIELDS)
            .eq("company_id", company_id).eq("preparation_id", str(case_id))
            .order("created_at", desc=True).limit(101).execute(),
            "External reference ledger",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("External reference ledger unavailable")
        raise HTTPException(status_code=503, detail="External reference ledger unavailable") from exc
    if len(rows) > 100:
        raise HTTPException(status_code=503, detail="External references exceed checked page")
    if any(str(r.get("company_id")) != company_id
           or str(r.get("preparation_id")) != str(case_id)
           or r.get("provenance_status") != "human_reported_unverified"
           for r in rows):
        raise HTTPException(status_code=503, detail="External reference source invalid")
    return {"items": rows, "agency_confirmation": False, "submission_permitted": False}


@router.post("/{case_id}/references", status_code=201)
async def add_human_external_reference(
    case_id: UUID,
    request: AddHumanExternalReferenceRequest,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    _require_owner_manager(user)
    if request.acknowledged_unverified is not True:
        raise HTTPException(status_code=422, detail="Unverified-reference acknowledgement required")
    company_id = str(user["company_id"])
    _read_case(db, company_id, case_id)
    policy_rule_id = await _allow_audited_action(
        db, user,
        action="compliance.price_external_reference_record",
        entity_type="compliance_price_posting_external_references",
        entity_id=str(case_id),
        changes={"reference_kind": request.kind, "unverified": True},
    )
    try:
        result = db.rpc("record_price_posting_external_reference", {
            "p_company_id": company_id,
            "p_preparation_id": str(case_id),
            "p_actor_id": str(user["sub"]),
            "p_reference_kind": request.kind,
            "p_external_reference": request.external_reference.strip(),
            "p_claimed_event_date": (
                request.claimed_event_date.isoformat() if request.claimed_event_date else None
            ),
            "p_note": request.note.strip(),
            "p_policy_effect": "allow",
            "p_policy_rule_id": policy_rule_id,
        }).execute()
        receipt = getattr(result, "data", None)
        if (not isinstance(receipt, dict)
            or receipt.get("provenance_status") != "human_reported_unverified"
            or receipt.get("agency_confirmation") is not False
            or receipt.get("submission_permitted") is not False
            or not receipt.get("reference_id")
            or not receipt.get("audit_event_id")):
            raise ValueError("External reference/audit receipt incomplete")
    except Exception as exc:
        log.error("Price external reference write unconfirmed error_type=%s",type(exc).__name__)
        raise HTTPException(status_code=503, detail="External evidence and audit not confirmed") from exc
    return receipt
