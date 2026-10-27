"""Fail-closed advisory gate for compliance rule evidence.

This module deliberately does not issue shipping authorizations. The legacy
rules engine is a diagnostic checklist; a separate authenticated, durable
human approval workflow is required before any operational clearance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from urllib.parse import urlparse

from .models import ComplianceCheckResult


class ReadinessStatus(str, Enum):
    BLOCKED = "blocked"
    SOURCE_REVIEW_REQUIRED = "source_review_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


@dataclass(frozen=True)
class RegulatoryEvidence:
    """Provenance of a *human-reviewed* jurisdiction/topic rule revision.

    A source URL is a reference, not proof the external source remains current.
    The review record must be created and audited by a trusted workflow.
    """

    company_id: str
    state_code: str
    topic: str
    source_url: str
    source_revision: str
    effective_on: date
    reviewed_on: date
    review_record_id: str

    def is_reviewed_for(
        self, company_id: str, state_code: str, as_of: date, max_review_age_days: int,
    ) -> bool:
        url = urlparse(self.source_url)
        return (
            self.company_id == company_id
            and self.state_code.upper() == state_code.upper()
            and bool(self.topic.strip())
            and url.scheme == "https"
            and bool(url.netloc)
            and bool(self.source_revision.strip())
            and bool(self.review_record_id.strip())
            and self.effective_on <= self.reviewed_on <= as_of
            and self.reviewed_on >= as_of - timedelta(days=max_review_age_days)
        )


@dataclass(frozen=True)
class ReadinessAssessment:
    status: ReadinessStatus
    authorized_to_ship: bool
    missing_topics: tuple[str, ...]
    actions: tuple[str, ...]


REQUIRED_TOPICS: tuple[str, ...] = (
    "federal_permit",
    "federal_label",
    "state_license",
    "state_distribution",
    "state_tax",
    "state_label_and_brand",
    "state_price_posting",
)


def assess_compliance_readiness(
    company_id: str,
    checklist: ComplianceCheckResult,
    evidence: list[RegulatoryEvidence],
    *,
    as_of: date,
    required_topics: tuple[str, ...] = REQUIRED_TOPICS,
    max_review_age_days: int = 180,
) -> ReadinessAssessment:
    """Return advisory readiness only; NEVER grant operational authorization.

    Gate A must provide a tenant-scoped rule registry, verified reviewer identity,
    source-revision validation and durable audit before API exposure.
    """
    if max_review_age_days < 0:
        raise ValueError("Maximum review age cannot be negative")
    if not company_id or checklist.quantity_cases <= 0 or not checklist.product_id or not checklist.state_code:
        return ReadinessAssessment(
            ReadinessStatus.BLOCKED,
            False,
            (),
            ("Correct missing shipment identity or non-positive case quantity",),
        )

    if checklist.blockers or not checklist.approved:
        return ReadinessAssessment(
            ReadinessStatus.BLOCKED,
            False,
            (),
            ("Resolve the compliance checklist blockers before requesting review",),
        )

    reviewed = {
        item.topic
        for item in evidence
        if item.is_reviewed_for(company_id, checklist.state_code, as_of, max_review_age_days)
    }
    missing = tuple(topic for topic in required_topics if topic not in reviewed)
    if missing:
        return ReadinessAssessment(
            ReadinessStatus.SOURCE_REVIEW_REQUIRED,
            False,
            missing,
            ("Attach and verify dated source revisions for each required rule topic",),
        )

    return ReadinessAssessment(
        ReadinessStatus.HUMAN_REVIEW_REQUIRED,
        False,
        (),
        ("Request human compliance approval through an authenticated audited workflow",),
    )
