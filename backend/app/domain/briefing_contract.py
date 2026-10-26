"""Gate A versioned contracts for role-scoped operational intelligence.

This module does NOT authorize or execute actions. ActionProposal/ActionDecision
in app.domain.contracts and tenant PolicyEngine remain authoritative for writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

BRIEFING_CONTRACT_VERSION = "1.0"

class BriefingSection(str, Enum):
    RED_ALERTS = "red_alerts"
    YELLOW_ALERTS = "yellow_alerts"
    OPPORTUNITIES = "opportunities"
    SCOREBOARD = "scoreboard"
    PRIORITIES = "todays_priorities"
    ACTIONS_TAKEN = "actions_taken"

SECTION_ORDER = tuple(BriefingSection)

DataQuality = Literal["verified", "partial", "unavailable"]

def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


@dataclass(frozen=True)
class EvidenceRef:
    """Lineage and quality of a particular source record, never an assertion of truth."""
    source_system: str
    source_record_id: str
    fetched_at: datetime
    quality: DataQuality
    confidence: int

    def __post_init__(self) -> None:
        if not self.source_system or not self.source_record_id:
            raise ValueError("Evidence requires source_system and source_record_id")
        _require_aware(self.fetched_at, "fetched_at")
        if self.quality not in ("verified", "partial", "unavailable"):
            raise ValueError("Unsupported evidence quality")
        if not 0 <= self.confidence <= 100:
            raise ValueError("confidence must be 0..100")
        if self.quality == "unavailable" and self.confidence != 0:
            raise ValueError("Unavailable evidence must have zero confidence")


@dataclass(frozen=True)
class DigestEntry:
    """A tenant-owned decision or visibility item scoped to authorized roles."""
    company_id: str
    entry_id: str
    section: BriefingSection
    title: str
    role_scope: frozenset[str]
    evidence: tuple[EvidenceRef, ...]
    detected_at: datetime
    proposed_action_id: str | None = None
    status: Literal["open", "resolved", "needs_review", "source_unavailable"] = "open"

    def __post_init__(self) -> None:
        if not self.company_id or not self.entry_id or not self.title:
            raise ValueError("A digest entry must have company_id, entry_id and title")
        if not self.role_scope or any(not role for role in self.role_scope):
            raise ValueError("role_scope must contain at least one role")
        _require_aware(self.detected_at, "detected_at")
        if not self.evidence:
            raise ValueError("Evidence is mandatory, even for an unavailable source")
        if self.status == "source_unavailable" and not any(
            e.quality == "unavailable" for e in self.evidence
        ):
            raise ValueError("source_unavailable entries require unavailable evidence")
        if any(e.quality == "unavailable" for e in self.evidence) and self.status == "resolved":
            raise ValueError("An unavailable source cannot substantiate resolution")


@dataclass(frozen=True)
class RoleBriefing:
    """Read-only projection. Call only AFTER trusted identity, tenant and role resolution."""
    company_id: str
    audience_role: str
    created_at: datetime
    entries: tuple[DigestEntry, ...]
    contract_version: str = BRIEFING_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.company_id or not self.audience_role:
            raise ValueError("Briefing requires company_id and audience_role")
        _require_aware(self.created_at, "created_at")
        if self.contract_version != BRIEFING_CONTRACT_VERSION:
            raise ValueError("Unsupported briefing contract version")
        for entry in self.entries:
            if entry.company_id != self.company_id:
                raise ValueError("Cross-tenant entries are prohibited")
            if self.audience_role not in entry.role_scope:
                raise ValueError("Briefing contains entry not authorized for this role")

    @classmethod
    def project(cls, *, company_id: str, audience_role: str,
                created_at: datetime, entries: list[DigestEntry]) -> "RoleBriefing":
        """Strict tenant scope; role projection does not broaden access."""
        if any(entry.company_id != company_id for entry in entries):
            raise ValueError("Cross-tenant entries are prohibited")
        allowed = tuple(
            entry for entry in entries if audience_role in entry.role_scope
        )
        return cls(company_id=company_id, audience_role=audience_role,
                   created_at=created_at, entries=allowed)

    def sections(self) -> dict[str, list[DigestEntry]]:
        """Stable six-section structure even when a section has no items."""
        return {
            section.value: [entry for entry in self.entries if entry.section == section]
            for section in SECTION_ORDER
        }
