"""Gate A: versioned role briefing contracts (pure, no network/DB)."""
from datetime import datetime, timezone

import pytest

from app.domain.briefing_contract import (
    BRIEFING_CONTRACT_VERSION, BriefingSection, DigestEntry, EvidenceRef,
    RoleBriefing,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def _evidence(quality="verified", confidence=90):
    return EvidenceRef("qb_sandbox", "invoice-01", NOW, quality, confidence)


def _entry(company="co-a", roles=frozenset({"owner", "finance"}),
           section=BriefingSection.RED_ALERTS, **kwargs):
    return DigestEntry(
        company_id=company, entry_id="e-1", section=section,
        title="Overdue invoice", role_scope=roles,
        evidence=(_evidence(),), detected_at=NOW, **kwargs,
    )


def test_six_sections_are_stable_and_versioned():
    briefing = RoleBriefing.project(
        company_id="co-a", audience_role="owner", created_at=NOW, entries=[_entry()]
    )
    assert briefing.contract_version == BRIEFING_CONTRACT_VERSION
    assert list(briefing.sections()) == [s.value for s in BriefingSection]
    assert len(briefing.sections()["red_alerts"]) == 1
    assert briefing.sections()["actions_taken"] == []


def test_role_projection_does_not_leak_unrelated_entries():
    entries = [_entry(), _entry(roles=frozenset({"logistics"}), section=BriefingSection.PRIORITIES)]
    briefing = RoleBriefing.project(
        company_id="co-a", audience_role="finance", created_at=NOW, entries=entries,
    )
    assert len(briefing.entries) == 1
    assert all("finance" in entry.role_scope for entry in briefing.entries)


def test_cross_tenant_projection_rejected_even_when_role_matches():
    with pytest.raises(ValueError, match="Cross-tenant"):
        RoleBriefing.project(company_id="co-a", audience_role="owner", created_at=NOW,
                             entries=[_entry(company="co-b")])


def test_direct_briefing_construction_rejects_unauthorized_role():
    with pytest.raises(ValueError, match="not authorized"):
        RoleBriefing(company_id="co-a", audience_role="sales", created_at=NOW,
                     entries=(_entry(),))


def test_missing_evidence_is_never_accepted_as_a_business_claim():
    with pytest.raises(ValueError, match="Evidence is mandatory"):
        DigestEntry(company_id="co-a", entry_id="e-2",
                    section=BriefingSection.SCOREBOARD, title="Revenue increased",
                    role_scope=frozenset({"owner"}), evidence=(), detected_at=NOW)


def test_unavailable_source_must_be_labeled_and_has_zero_confidence():
    unavailable = _evidence("unavailable", 0)
    entry = DigestEntry(company_id="co-a", entry_id="e-3",
                        section=BriefingSection.YELLOW_ALERTS,
                        title="Accounting source unavailable",
                        role_scope=frozenset({"owner"}), evidence=(unavailable,),
                        detected_at=NOW, status="source_unavailable")
    assert entry.status == "source_unavailable"
    with pytest.raises(ValueError, match="zero confidence"):
        _evidence("unavailable", 50)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        EvidenceRef("qb", "1", datetime(2026, 10, 26), "verified", 90)
