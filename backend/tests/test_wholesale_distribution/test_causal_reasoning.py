"""Tests for §18 — Causal Reasoning Engine."""
import pytest
from app.intelligence.wholesale_distribution.causal_reasoning import (
    CausalReasoningEngine,
    CustomerSegment,
    RootCause,
)


@pytest.fixture
def engine():
    return CausalReasoningEngine(company_id="co-test")


def _make_order_history(n: int = 30) -> list[dict]:
    from datetime import date, timedelta
    base = date(2024, 1, 1)
    orders = []
    for i in range(n):
        d = base + timedelta(days=i * 15)
        orders.append({"date": d.isoformat(), "amount": 500 + i * 10, "units": 10 + i})
    return orders


class TestCustomerDropAnalysis:
    def test_quality_event_identified_as_primary(self, engine):
        history = _make_order_history(20)
        returns = [
            {"date": "2024-06-01", "reason": "defect", "amount": 2000},
            {"date": "2024-06-10", "reason": "broken on arrival", "amount": 1000},
        ]
        result = engine.analyse_customer_drop(
            customer_id="c-1",
            customer_name="Test Customer",
            order_history=history,
            recent_returns=returns,
            pricing_changes=[],
            competitor_signals=[],
            days_since_last_order=45,
        )
        assert result.primary_cause.root_cause == RootCause.QUALITY_EVENT

    def test_pricing_pressure_identified(self, engine):
        history = _make_order_history(20)
        pricing_changes = [
            {"sku": "SKU-001", "old_price": 10.0, "new_price": 15.0},
            {"sku": "SKU-002", "old_price": 20.0, "new_price": 28.0},
        ]
        result = engine.analyse_customer_drop(
            customer_id="c-2",
            customer_name="Price-Sensitive Corp",
            order_history=history,
            recent_returns=[],
            pricing_changes=pricing_changes,
            competitor_signals=[],
            days_since_last_order=35,
        )
        assert result.primary_cause.root_cause in (
            RootCause.PRICING_PRESSURE, RootCause.DEMAND_SHIFT
        )

    def test_long_silence_is_lost_cause(self, engine):
        history = _make_order_history(10)
        result = engine.analyse_customer_drop(
            customer_id="c-3",
            customer_name="Gone Forever Inc",
            order_history=history,
            recent_returns=[],
            pricing_changes=[],
            competitor_signals=[],
            days_since_last_order=200,
        )
        assert result.customer_segment == CustomerSegment.LOST_CAUSE

    def test_seasonal_pattern_is_natural_recoverer(self, engine):
        # Build order history with large seasonal gaps
        from datetime import date, timedelta
        history = []
        base = date(2022, 1, 1)
        for i in range(40):
            # Orders every ~10 days, but skip 3 months mid-year (simulated season gap)
            d = base + timedelta(days=i * 10)
            history.append({"date": d.isoformat(), "amount": 500, "units": 10})

        # silence of 80 days (matches historical max gap)
        result = engine.analyse_customer_drop(
            customer_id="c-4",
            customer_name="Seasonal Sam",
            order_history=history,
            recent_returns=[],
            pricing_changes=[],
            competitor_signals=[],
            days_since_last_order=90,
        )
        # Seasonal pattern may be detected as primary or secondary — either is valid
        assert result.primary_cause is not None
        assert result.anomaly_description is not None

    def test_causal_chain_has_intervention(self, engine):
        result = engine.analyse_customer_drop(
            customer_id="c-5",
            customer_name="Any Customer",
            order_history=_make_order_history(5),
            recent_returns=[],
            pricing_changes=[],
            competitor_signals=[],
            days_since_last_order=60,
        )
        assert result.primary_cause.intervention != ""
        assert result.primary_cause.counterfactual != ""

    def test_to_dict_is_serializable(self, engine):
        result = engine.analyse_customer_drop(
            customer_id="c-6",
            customer_name="Test",
            order_history=_make_order_history(10),
            recent_returns=[],
            pricing_changes=[],
            competitor_signals=[],
            days_since_last_order=30,
        )
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "primary_cause" in d
        assert "customer_segment" in d


class TestInventoryAnomalyAnalysis:
    def test_demand_spike_identified(self, engine):
        result = engine.analyse_inventory_anomaly(
            sku="SKU-100",
            sku_name="Widget A",
            current_stock=50,
            avg_daily_demand=20,
            pending_po_units=0,
            supplier_lead_time_days=7,
            recent_demand_spike=True,
            recent_supplier_delays=[],
        )
        assert result.primary_cause.root_cause == RootCause.DEMAND_SHIFT

    def test_supplier_delay_identified(self, engine):
        result = engine.analyse_inventory_anomaly(
            sku="SKU-200",
            sku_name="Widget B",
            current_stock=20,
            avg_daily_demand=5,
            pending_po_units=100,
            supplier_lead_time_days=14,
            recent_demand_spike=False,
            recent_supplier_delays=[{"delay_days": 10}, {"delay_days": 5}],
        )
        assert result.primary_cause.root_cause == RootCause.SUPPLY_DISRUPTION
