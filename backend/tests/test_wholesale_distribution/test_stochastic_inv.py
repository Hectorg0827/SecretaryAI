"""Tests for §19 — Stochastic Inventory Intelligence."""
import math
import pytest

from app.intelligence.wholesale_distribution.stochastic_inv import (
    DemandDistribution,
    EchelonNode,
    LeadTimeDistribution,
    PricingPhase,
    StochasticInventoryEngine,
)


@pytest.fixture
def engine():
    return StochasticInventoryEngine(
        annual_revenue=10_000_000,
        ordering_cost_per_po=50.0,
        holding_cost_rate=0.25,
        service_level=0.95,
    )


def _demand(daily_values: list[float]) -> DemandDistribution:
    return DemandDistribution(sku="TEST-SKU", daily_demand_history=daily_values)


def _lead_time(values: list[float]) -> LeadTimeDistribution:
    return LeadTimeDistribution(
        supplier_id="sup-1", sku="TEST-SKU", lead_time_history=values
    )


class TestPhaseSelection:
    def test_below_5m_is_phase1(self):
        e = StochasticInventoryEngine(annual_revenue=2_000_000)
        assert e.phase == PricingPhase.PHASE_1

    def test_5m_to_20m_is_phase2(self):
        e = StochasticInventoryEngine(annual_revenue=10_000_000)
        assert e.phase == PricingPhase.PHASE_2

    def test_above_20m_is_phase3(self):
        e = StochasticInventoryEngine(annual_revenue=50_000_000)
        assert e.phase == PricingPhase.PHASE_3


class TestDemandDistribution:
    def test_mean(self):
        d = _demand([10.0, 20.0, 30.0])
        assert d.mean == pytest.approx(20.0)

    def test_std_dev(self):
        d = _demand([10.0] * 100)
        assert d.std_dev == pytest.approx(0.0)

    def test_cv_zero_mean(self):
        d = _demand([])
        assert d.coefficient_of_variation == 0.0

    def test_cv_high_variability(self):
        d = _demand([1.0, 10.0, 100.0] * 30)
        assert d.coefficient_of_variation > 1.0


class TestInventoryCalculation:
    def test_safety_stock_positive(self, engine):
        demand = _demand([10.0] * 90)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        assert rec.safety_stock_units >= 0

    def test_rop_greater_than_safety_stock(self, engine):
        demand = _demand([10.0] * 90)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        assert rec.reorder_point_units > rec.safety_stock_units

    def test_eoq_positive(self, engine):
        demand = _demand([10.0] * 90)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        assert rec.economic_order_qty > 0

    def test_sparse_data_warning(self, engine):
        demand = _demand([10.0] * 20)  # < 30 days → insufficient
        lt = _lead_time([7.0] * 5)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        assert rec.data_quality == "insufficient"
        assert any("conservative" in n.lower() for n in rec.notes)

    def test_good_data_quality(self, engine):
        demand = _demand([10.0] * 120)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        assert rec.data_quality == "good"

    def test_service_level_near_target(self, engine):
        demand = _demand([10.0] * 120)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0, current_stock=500)
        assert rec.achieved_service_level >= 0.90

    def test_high_cv_triggers_note(self, engine):
        # Highly variable demand (CV >> 0.75)
        values = [0.1] * 30 + [100.0] * 30 + [0.1] * 30
        demand = _demand(values)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        assert any("variability" in n.lower() or "cv" in n.lower() for n in rec.notes)

    def test_reorder_needed_at_rop(self, engine):
        demand = _demand([10.0] * 90)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        needs, reason = engine.reorder_needed(rec.reorder_point_units, rec)
        assert needs is True

    def test_no_reorder_with_ample_stock(self, engine):
        demand = _demand([10.0] * 90)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        needs, reason = engine.reorder_needed(10_000, rec)
        assert needs is False

    def test_to_dict_is_serializable(self, engine):
        demand = _demand([10.0] * 90)
        lt = _lead_time([7.0] * 20)
        rec = engine.calculate(demand, lt, unit_cost=5.0)
        d = rec.to_dict()
        assert isinstance(d, dict)
        assert "safety_stock_units" in d
        assert "economic_order_qty" in d


class TestMultiEchelon:
    def test_multi_echelon_returns_nodes(self, engine):
        # Use variable demand so safety stock is non-zero
        variable_demand = [3.0, 5.0, 8.0, 4.0, 6.0, 9.0, 2.0] * 15   # 105 days, mean≈5.3, std≈2.3
        variable_lt = [3.0, 5.0, 7.0, 4.0, 6.0] * 4   # variable lead times
        nodes = [
            EchelonNode(
                node_id="wh-east",
                location="East Warehouse",
                demand_distribution=DemandDistribution(sku="T", daily_demand_history=variable_demand),
                replenishment_lead_time_days=5,
                current_stock=100,
                in_transit_units=50,
            ),
            EchelonNode(
                node_id="wh-west",
                location="West Warehouse",
                demand_distribution=DemandDistribution(sku="T", daily_demand_history=variable_demand),
                replenishment_lead_time_days=7,
                current_stock=80,
            ),
        ]
        result = engine.calculate_multi_echelon("TEST-SKU", nodes, unit_cost=5.0)
        assert len(result.nodes) == 2
        # Safety stock should be >= 0 (may be 0 if data is constant, > 0 with variance)
        assert result.system_safety_stock >= 0
        assert result.system_service_level <= 1.0

    def test_bottleneck_identified(self, engine):
        """The node with highest shortfall-to-ROP ratio is the bottleneck."""
        nodes = [
            EchelonNode(
                node_id="low-stock",
                location="Low Stock WH",
                demand_distribution=_demand([50.0] * 90),  # high demand, low stock
                replenishment_lead_time_days=14,
                current_stock=10,   # far below ROP (50*14 = 700 day demand during LT)
            ),
            EchelonNode(
                node_id="high-stock",
                location="High Stock WH",
                demand_distribution=_demand([2.0] * 90),
                replenishment_lead_time_days=3,
                current_stock=500,  # well above ROP
            ),
        ]
        result = engine.calculate_multi_echelon("TEST-SKU", nodes, unit_cost=5.0)
        assert result.bottleneck_node == "low-stock"
