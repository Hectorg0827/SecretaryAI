"""Gate A: public read-only executive briefing response contract v1.

Owned by Chat 1. Frontend, mobile and desktop teams should consume the
stable OpenAPI schema rather than reverse-engineer dashboard snapshots.

The v1 route describes PARTIAL coverage, not a verified daily digest.
The classes have no side effects or provider dependencies.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BriefingEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    fetched_at: datetime
    quality: Literal["verified", "partial", "unavailable"]
    confidence: int = Field(ge=0, le=100)


class BriefingEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1)
    section: Literal[
        "red_alerts", "yellow_alerts", "opportunities", "scoreboard",
        "todays_priorities", "actions_taken",
    ]
    title: str = Field(min_length=1)
    status: Literal["open", "resolved", "needs_review", "source_unavailable"]
    detected_at: datetime
    proposed_action_id: str | None = None
    evidence: list[BriefingEvidenceV1] = Field(min_length=1)


class BriefingSectionsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    red_alerts: list[BriefingEntryV1]
    yellow_alerts: list[BriefingEntryV1]
    opportunities: list[BriefingEntryV1]
    scoreboard: list[BriefingEntryV1]
    todays_priorities: list[BriefingEntryV1]
    actions_taken: list[BriefingEntryV1]


class BriefingTodayResponseV1(BaseModel):
    """Typed six-section projection for owner/manager only.

    Eligibility and tenant scoping MUST be established before construction.
    complete_digest=False and source_quality=partial are deliberate and must
    not be silently promoted by an unverified client.
    """
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"]
    company_id: str = Field(min_length=1)
    audience_role: Literal["owner", "manager"]
    created_at: datetime
    sections: BriefingSectionsV1
    source_coverage: Literal["dashboard_snapshot_only"]
    complete_digest: Literal[False]
    source_quality: Literal["partial"]
    snapshot_generated_at: datetime
    snapshot_age_seconds: float = Field(ge=0)
