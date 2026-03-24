"""Tests for §20 — Action Object schema + Progressive Autonomy engine."""
import pytest
from datetime import datetime, timezone, timedelta

from app.intelligence.wholesale_distribution.action_schema import (
    ActionImpact,
    ActionObject,
    ActionType,
    AutonomyEngine,
    Guardrails,
)


def _make_action(
    action_type=ActionType.REORDER,
    confidence=0.90,
    risk="medium",
    expiry_hours=48,
) -> ActionObject:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=expiry_hours)).isoformat()
    return ActionObject(
        type=action_type,
        title="Test action",
        description="Test description",
        confidence=confidence,
        risk=risk,
        impact=ActionImpact(cost_delta=1000.0, units=50),
        expiration=expiry,
    )


class TestActionObject:
    def test_to_dict_includes_all_fields(self):
        action = _make_action()
        d = action.to_dict()
        assert "id" in d
        assert "type" in d
        assert "confidence" in d
        assert "impact" in d
        assert "audit_trail" in d

    def test_record_adds_audit_entry(self):
        action = _make_action()
        action.record("approved", actor="user-123", note="LGTM")
        assert len(action.audit_trail) == 1
        entry = action.audit_trail[0]
        assert entry.event == "approved"
        assert entry.actor == "user-123"


class TestGuardrails:
    def test_low_risk_reorder_within_rails(self):
        g = Guardrails(max_reorder_value=5_000.0)
        action = _make_action(risk="low")
        action.impact.cost_delta = 2_000.0
        assert g.action_within_rails(action) is True

    def test_high_risk_blocked(self):
        g = Guardrails()
        action = _make_action(risk="high")
        assert g.action_within_rails(action) is False

    def test_critical_risk_blocked(self):
        g = Guardrails()
        action = _make_action(risk="critical")
        assert g.action_within_rails(action) is False

    def test_reorder_above_max_blocked(self):
        g = Guardrails(max_reorder_value=1_000.0)
        action = _make_action(risk="low")
        action.impact.cost_delta = 2_000.0
        assert g.action_within_rails(action) is False

    def test_pricing_above_max_pct_blocked(self):
        g = Guardrails(max_pricing_change_pct=5.0)
        action = _make_action(action_type=ActionType.PRICING_ADJUSTMENT, risk="low")
        action.context["price_change_pct"] = 10.0
        assert g.action_within_rails(action) is False


class TestAutonomyEngine:
    def test_tier1_always_informs(self):
        engine = AutonomyEngine(tier=1)
        action = _make_action(confidence=0.99, risk="low")
        result = engine.evaluate(action)
        assert result["disposition"] == "inform"

    def test_tier2_always_drafts(self):
        engine = AutonomyEngine(tier=2)
        action = _make_action(confidence=0.99, risk="low")
        result = engine.evaluate(action)
        assert result["disposition"] == "draft"

    def test_tier3_executes_within_rails(self):
        engine = AutonomyEngine(tier=3, guardrails=Guardrails(max_reorder_value=10_000))
        action = _make_action(confidence=0.90, risk="low")
        action.impact.cost_delta = 500.0
        result = engine.evaluate(action)
        assert result["disposition"] == "execute"

    def test_tier3_drafts_outside_rails(self):
        engine = AutonomyEngine(tier=3, guardrails=Guardrails(max_reorder_value=100))
        action = _make_action(confidence=0.90, risk="low")
        action.impact.cost_delta = 5_000.0
        result = engine.evaluate(action)
        assert result["disposition"] == "draft"

    def test_tier4_executes_non_critical(self):
        engine = AutonomyEngine(tier=4)
        action = _make_action(confidence=0.80, risk="medium")
        result = engine.evaluate(action)
        assert result["disposition"] == "execute"

    def test_tier4_escalates_critical(self):
        engine = AutonomyEngine(tier=4)
        action = _make_action(confidence=0.80, risk="critical")
        result = engine.evaluate(action)
        assert result["disposition"] == "escalate"

    def test_low_confidence_always_drafts(self):
        """Any tier + confidence < 0.65 → draft."""
        for tier in [3, 4]:
            engine = AutonomyEngine(tier=tier)
            action = _make_action(confidence=0.50, risk="low")
            result = engine.evaluate(action)
            assert result["disposition"] == "draft", f"Tier {tier} should draft on low confidence"

    def test_expired_action_returns_inform(self):
        engine = AutonomyEngine(tier=4)
        action = _make_action(expiry_hours=-1)  # already expired
        result = engine.evaluate(action)
        assert result["disposition"] == "inform"
        assert action.status == "expired"
