"""Staging-only tests: all business evidence is synthetic; no network or writes."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence.lola.orchestrator import (
    DecisionState, GatePolicy, LaneEvidence, WakeTrigger, evaluate_event,
)

NOW = datetime(2026, 11, 14, 20, tzinfo=timezone.utc)
TRIGGER = WakeTrigger("tenant-A", "shipment-A", "CAB-750ML", "wake-1")


def lane(name, values, **changes):
    evidence = replace(
        LaneEvidence(name, TRIGGER.company_id, TRIGGER.shipment_ref,
                     TRIGGER.sku, f"{name}-record", "rev1", NOW, True, values),
        **changes,
    )
    async def reader(_trigger):
        return evidence
    return reader


def logistics(actual="1840.00", baseline="1200.00", **changes):
    return lane("logistics", {"drayage_actual_usd": actual,
                             "drayage_baseline_usd": baseline}, **changes)


def finance(variance="640.00", before="38.20", after="35.80", **changes):
    return lane("finance", {"cost_variance_usd": variance,
                            "gross_margin_before_pct": before,
                            "gross_margin_after_pct": after,
                            "suggested_price_per_case_usd": "68.50"}, **changes)


@pytest.mark.asyncio
async def test_reconciled_exception_only_proposes_review():
    result = await evaluate_event(TRIGGER, logistics_lane=logistics(), finance_lane=finance(), now=NOW)
    assert result.state == DecisionState.REVIEW_REQUIRED
    assert set(result.reasons) == {"cost_overrun_threshold", "margin_erosion_threshold"}
    assert result.proposal.cost_variance_usd == "640.00"
    assert result.proposal.margin_erosion_bps == "240.00"
    assert result.proposal.status == "proposal_only_not_persisted"
    assert not result.external_writes_executed and not result.notifications_sent


@pytest.mark.asyncio
async def test_nominal_verified_event_stays_silent():
    result = await evaluate_event(
        TRIGGER, logistics_lane=logistics("1220", "1200"),
        finance_lane=finance("20", "38.2", "38.0"), now=NOW,
    )
    assert result.state == DecisionState.SILENT and result.proposal is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"verified": False}, {"company_id": "tenant-B"},
    {"source_revision": ""}, {"observed_at": NOW - timedelta(days=3)},
])
async def test_untrusted_finance_never_passes_silent_gate(changes):
    result = await evaluate_event(
        TRIGGER, logistics_lane=logistics("1200", "1200"),
        finance_lane=finance("0", "38.2", "38.2", **changes), now=NOW,
    )
    assert result.state == DecisionState.EVIDENCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_disagreement_fails_closed():
    result = await evaluate_event(
        TRIGGER, logistics_lane=logistics(), finance_lane=finance("10"), now=NOW,
    )
    assert result.state == DecisionState.EVIDENCE_UNAVAILABLE and result.proposal is None


@pytest.mark.asyncio
async def test_timeout_is_not_nominal():
    async def hanging(_):
        await asyncio.sleep(.1)
    result = await evaluate_event(
        TRIGGER, logistics_lane=hanging, finance_lane=finance(),
        policy=GatePolicy(lane_timeout_seconds=.01), now=NOW,
    )
    assert result.state == DecisionState.EVIDENCE_UNAVAILABLE
    assert "logistics_source_unavailable" in result.reasons


@pytest.mark.asyncio
async def test_portal_outage_does_not_hide_finance_exception():
    async def offline(_):
        raise TimeoutError("carrier portal down")
    result = await evaluate_event(
        TRIGGER, logistics_lane=logistics(), finance_lane=finance(),
        portal_lane=offline, now=NOW,
    )
    assert result.state == DecisionState.REVIEW_REQUIRED
    assert "portal_source_unavailable" in result.reasons
    assert "cost_overrun_threshold" in result.reasons


@pytest.mark.asyncio
async def test_terminal_hold_always_requires_review():
    result = await evaluate_event(
        TRIGGER, logistics_lane=logistics("1200", "1200"),
        finance_lane=finance("0", "38.2", "38.2"),
        portal_lane=lane("portal", {"hold_present": "true"}), now=NOW,
    )
    assert result.state == DecisionState.REVIEW_REQUIRED
    assert "terminal_hold_present" in result.reasons
