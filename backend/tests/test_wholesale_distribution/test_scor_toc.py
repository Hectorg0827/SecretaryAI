"""Tests for §22 — SCOR model + Theory of Constraints."""
import pytest
from app.intelligence.wholesale_distribution.scor_toc import (
    ConstraintType,
    ProcessNode,
    SCORDomain,
    SCORToCEngine,
)


@pytest.fixture
def engine():
    return SCORToCEngine(company_id="co-test")


def _make_orders(n: int = 50, on_time: bool = True, fill_pct: float = 1.0) -> list[dict]:
    from datetime import date, timedelta
    orders = []
    base = date(2024, 1, 1)
    for i in range(n):
        ordered = base + timedelta(days=i)
        delivered = base + timedelta(days=i + (2 if on_time else 8))
        promised = base + timedelta(days=i + 5)
        lines_ordered = 10
        lines_filled = int(lines_ordered * fill_pct)
        orders.append({
            "id": f"ord-{i}",
            "ordered_at": ordered.isoformat(),
            "delivered_at": delivered.isoformat(),
            "promised_at": promised.isoformat(),
            "lines_ordered": lines_ordered,
            "lines_filled": lines_filled,
            "damaged": False,
        })
    return orders


class TestSCORMetrics:
    def test_perfect_order_fulfillment_high_on_time(self, engine):
        orders = _make_orders(100, on_time=True, fill_pct=1.0)
        m = engine.compute_metrics(
            orders=orders,
            inventory_value=500_000,
            cogs=2_000_000,
            revenue=3_000_000,
            ar_balance=200_000,
            ap_balance=100_000,
            supply_chain_costs=400_000,
            supplier_lead_times=[7.0] * 20,
        )
        assert m.perfect_order_fulfillment_pct > 90

    def test_fill_rate_reflects_lines_filled(self, engine):
        orders = _make_orders(50, fill_pct=0.80)
        m = engine.compute_metrics(
            orders=orders,
            inventory_value=100_000,
            cogs=500_000,
            revenue=700_000,
            ar_balance=50_000,
            ap_balance=30_000,
            supply_chain_costs=80_000,
            supplier_lead_times=[5.0] * 10,
        )
        assert m.order_fill_rate_pct == pytest.approx(80.0, abs=1)

    def test_cash_to_cash_cycle_formula(self, engine):
        # C2C = DIO + DSO - DPO
        orders = _make_orders(20)
        m = engine.compute_metrics(
            orders=orders,
            inventory_value=1_000_000,
            cogs=4_000_000,   # DIO = (1M/4M) * 365 = 91.25
            revenue=6_000_000,
            ar_balance=500_000,   # DSO = (500K/6M) * 365 = 30.4
            ap_balance=200_000,   # DPO = (200K/4M) * 365 = 18.25
            supply_chain_costs=600_000,
            supplier_lead_times=[10.0] * 10,
        )
        expected_c2c = m.days_inventory_outstanding + m.days_sales_outstanding - m.days_payable_outstanding
        assert m.cash_to_cash_cycle_days == pytest.approx(expected_c2c, abs=0.01)

    def test_benchmark_returns_ratings(self, engine):
        orders = _make_orders(50)
        m = engine.compute_metrics(
            orders=orders,
            inventory_value=200_000, cogs=1_000_000, revenue=1_500_000,
            ar_balance=100_000, ap_balance=50_000,
            supply_chain_costs=120_000, supplier_lead_times=[5.0] * 5,
        )
        benchmarks = m.benchmark_vs_industry()
        valid_ratings = {"excellent", "good", "fair", "poor"}
        for rating in benchmarks.values():
            assert rating in valid_ratings


class TestToC:
    def _make_node(
        self, node_id: str, name: str, utilization: float, domain=SCORDomain.DELIVER
    ) -> ProcessNode:
        return ProcessNode(
            node_id=node_id,
            name=name,
            domain=domain,
            throughput_per_day=100,
            queue_depth=utilization * 10,
            utilization_pct=utilization,
            avg_cycle_time_hours=8.0,
        )

    def test_high_utilization_is_constraint(self, engine):
        nodes = [
            self._make_node("n1", "Warehouse Pick", 95),
            self._make_node("n2", "Order Entry", 60),
        ]
        constraints = engine.identify_constraints(nodes)
        assert len(constraints) == 1
        assert constraints[0].node.node_id == "n1"

    def test_no_constraint_below_80pct(self, engine):
        nodes = [
            self._make_node("n1", "Order Entry", 70),
            self._make_node("n2", "Carrier Dispatch", 75),
        ]
        constraints = engine.identify_constraints(nodes)
        assert len(constraints) == 0

    def test_critical_severity_at_high_utilization(self, engine):
        nodes = [self._make_node("n1", "Supplier Lead Time", 99.5)]
        constraints = engine.identify_constraints(nodes)
        assert constraints[0].severity == "critical"

    def test_exploit_actions_present(self, engine):
        nodes = [self._make_node("n1", "Warehouse Picking", 92)]
        constraints = engine.identify_constraints(nodes)
        assert len(constraints[0].exploit_actions) > 0

    def test_correct_constraint_type_mapped(self, engine):
        nodes = [self._make_node("n1", "Customs Clearance", 90)]
        constraints = engine.identify_constraints(nodes)
        assert constraints[0].constraint_type == ConstraintType.CUSTOMS_CLEARANCE

    def test_full_analysis_returns_report(self, engine):
        orders = _make_orders(20)
        nodes = [self._make_node("n1", "Carrier Dispatch", 93)]
        report = engine.full_analysis(
            orders=orders,
            inventory_value=100_000,
            cogs=500_000,
            revenue=700_000,
            ar_balance=50_000,
            ap_balance=30_000,
            supply_chain_costs=80_000,
            supplier_lead_times=[7.0] * 5,
            process_nodes=nodes,
        )
        d = report.to_dict()
        assert "scor_metrics" in d
        assert "primary_constraint" in d
        assert d["primary_constraint"] is not None
