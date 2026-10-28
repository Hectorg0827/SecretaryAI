"""Gate 3.6: analytical engines cannot invent certainty from missing evidence."""

import math

import pytest

from app.intelligence.wholesale_distribution.esg_tco import ESGTCOEngine
from app.intelligence.wholesale_distribution.scor_toc import (
    ProcessNode, SCORDomain, SCORToCEngine,
)
from app.intelligence.wholesale_distribution.stochastic_inv import (
    DemandDistribution, EchelonNode, LeadTimeDistribution, StochasticInventoryEngine,
)


def _demand(values):
    return DemandDistribution(sku="SKU", daily_demand_history=values)


def _lead(values):
    return LeadTimeDistribution(supplier_id="SUP", sku="SKU", lead_time_history=values)


def test_deterministic_stockout_probability_has_correct_boundary():
    engine = StochasticInventoryEngine()
    assert engine._normal_ccdf(0, 70, 0) == 1.0
    assert engine._normal_ccdf(70, 70, 0) == 0.0
    assert engine._normal_ccdf(100, 70, 0) == 0.0
    rec = engine.calculate(_demand([10] * 120), _lead([7] * 20), 5, current_stock=0)
    assert rec.stockout_probability_without_ss == 1.0


@pytest.mark.parametrize("demand,lead", [
    ([], [7, 8]),
    ([10] * 90, []),
])
def test_empty_demand_or_supplier_lead_observations_fail_closed(demand, lead):
    with pytest.raises(ValueError, match="observ"):
        StochasticInventoryEngine().calculate(_demand(demand), _lead(lead), 5)


@pytest.mark.parametrize("demand,lead,cost", [
    ([10, -1], [7, 8], 5),
    ([10, math.nan], [7, 8], 5),
    ([10, 11], [7, math.inf], 5),
    ([10, 11], [7, 8], -5),
])
def test_stochastic_rejects_invalid_observations(demand, lead, cost):
    with pytest.raises(ValueError):
        StochasticInventoryEngine().calculate(_demand(demand), _lead(lead), cost)


def test_sparse_supplier_history_downgrades_quality_and_labels_modeled_service():
    rec = StochasticInventoryEngine().calculate(_demand([10] * 120), _lead([7]), 5)
    assert rec.data_quality == "insufficient"
    assert rec.to_dict()["supplier_lead_time_observations"] == 1
    assert rec.to_dict()["demand_observations"] == 120
    assert rec.to_dict()["service_level_is_observed"] is False
    assert any("lead" in note.lower() for note in rec.notes)


def test_multi_echelon_risk_accounts_for_in_transit_stock_and_bounded_service():
    engine = StochasticInventoryEngine()
    nodes = [EchelonNode(
        node_id="WH-1", location="WH-1",
        demand_distribution=_demand([10] * 100),
        replenishment_lead_time_days=7,
        current_stock=0,
        in_transit_units=1000,
    )]
    result = engine.calculate_multi_echelon("SKU", nodes, unit_cost=5)
    assert result.nodes[0]["effective_stock"] == 1000
    assert result.nodes[0]["stockout_probability"] == 0.0
    assert 0.0 <= result.system_service_level <= 1.0


def test_scor_unknown_delivery_not_counted_as_perfect_order():
    engine = SCORToCEngine(company_id="company-1")
    metrics = engine.compute_metrics(
        orders=[{"id": "ORDER", "ordered_at": "2026-10-01",
                 "lines_ordered": 10, "lines_filled": 10, "damaged": False}],
        inventory_value=100, cogs=1000, revenue=1500, ar_balance=50,
        ap_balance=30, supply_chain_costs=100, supplier_lead_times=[],
    )
    assert metrics.perfect_order_fulfillment_pct == 0
    assert metrics.to_dict()["evidence"]["verified_delivery_orders"] == 0
    assert metrics.benchmark_vs_industry()["perfect_order_fulfillment"] == "insufficient_data"


def test_scor_empty_activity_does_not_claim_good_benchmarks():
    engine = SCORToCEngine(company_id="company-1")
    report = engine.full_analysis(
        orders=[], inventory_value=0, cogs=0, revenue=0, ar_balance=0,
        ap_balance=0, supply_chain_costs=0, supplier_lead_times=[],
        process_nodes=[],
    )
    data = report.to_dict()
    assert data["benchmarks"]["order_fill_rate"] == "insufficient_data"
    assert data["benchmarks"]["cash_to_cash"] == "insufficient_data"
    assert data["scor_domain_scores"]["make"] is None
    assert any("evidence" in reason.lower() for reason in data["improvement_priority"])


def test_toc_high_utilization_without_backlog_is_not_proven_constraint():
    engine = SCORToCEngine(company_id="company-1")
    nodes = [ProcessNode(
        node_id="WH-1", name="Warehouse Picking", domain=SCORDomain.DELIVER,
        throughput_per_day=100, queue_depth=0, utilization_pct=99,
        avg_cycle_time_hours=2,
    )]
    assert engine.identify_constraints(nodes) == []


@pytest.mark.parametrize("utilization,throughput,queue", [
    (120, 100, 5),
    (95, 0, 5),
    (95, 100, -1),
    (math.nan, 100, 5),
])
def test_toc_rejects_invalid_process_observations(utilization, throughput, queue):
    engine = SCORToCEngine(company_id="company-1")
    nodes = [ProcessNode(
        node_id="WH-1", name="Warehouse Picking", domain=SCORDomain.DELIVER,
        throughput_per_day=throughput, queue_depth=queue, utilization_pct=utilization,
        avg_cycle_time_hours=2,
    )]
    with pytest.raises(ValueError):
        engine.identify_constraints(nodes)


def test_toc_gain_numbers_are_labeled_unverified_heuristics():
    engine = SCORToCEngine(company_id="company-1")
    result = engine.identify_constraints([ProcessNode(
        node_id="WH-1", name="Warehouse Picking", domain=SCORDomain.DELIVER,
        throughput_per_day=100, queue_depth=30, utilization_pct=99,
        avg_cycle_time_hours=2,
    )])[0].to_dict()
    assert result["gain_is_measured"] is False
    assert result["throughput_loss_is_measured"] is False
    assert result["confidence"] == "low_unverified_heuristic"


def _supplier(supplier_id, price, volume=1000):
    return {
        "supplier_id": supplier_id, "annual_volume": volume,
        "unit_of_measure": "case", "currency": "USD",
        "purchase_price": price, "inbound_freight_per_unit": 1.5,
        "customs_duty_rate_pct": 10, "brokerage_per_unit": 0.3,
        "days_inventory_outstanding": 30,
        "holding_cost_rate_pct": 25, "return_rate_pct": 1,
        "return_handling_cost": 8, "replacement_cost_per_return": 4,
        "kg_co2e_per_unit": 1.0, "volume_rebate_pct": 0,
    }


def test_tco_missing_freight_or_duties_cannot_win_supplier_recommendation():
    engine = ESGTCOEngine(company_id="company-1")
    with pytest.raises(ValueError, match="missing"):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10),
            {"supplier_id": "CHEAP", "annual_volume": 1000, "purchase_price": 6},
        ], current_supplier_id="CURRENT")


@pytest.mark.parametrize("volume,rate", [
    (0, 10),
    (1000, -10),
    (1000, math.nan),
    (1000, 110),
])
def test_tco_refuses_missing_volume_invalid_cost_rates(volume, rate):
    engine = ESGTCOEngine(company_id="company-1")
    with pytest.raises(ValueError):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10),
            _supplier("OTHER", 8, volume=volume) | {"customs_duty_rate_pct": rate},
        ], current_supplier_id="CURRENT")


def test_tco_only_compares_equal_verified_volume_and_known_current_supplier():
    engine = ESGTCOEngine(company_id="company-1")
    with pytest.raises(ValueError):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10, volume=1000),
            _supplier("OTHER", 8, volume=500),
        ], current_supplier_id="CURRENT")
    with pytest.raises(ValueError):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10), _supplier("OTHER", 8),
        ], current_supplier_id="NOT_PRESENT")


def test_tco_malformed_volume_fails_with_clear_validation():
    engine = ESGTCOEngine(company_id="company-1")
    with pytest.raises(ValueError):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10),
            _supplier("OTHER", 8) | {"annual_volume": []},
        ], current_supplier_id="CURRENT")


@pytest.mark.parametrize("other", [
    {"unit_of_measure": "bottle"},
    {"unit_of_measure": ""},
    {"currency": "EUR"},
    {"currency": ""},
])
def test_tco_refuses_mixed_or_unknown_cost_units_and_currencies(other):
    engine = ESGTCOEngine(company_id="company-1")
    with pytest.raises(ValueError, match="unit|currency|USD"):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10), _supplier("OTHER", 8) | other,
        ], current_supplier_id="CURRENT")


def test_tco_requires_explicit_unit_and_currency_on_all_suppliers():
    engine = ESGTCOEngine(company_id="company-1")
    with pytest.raises(ValueError, match="missing"):
        engine.compare_suppliers("SKU", [
            _supplier("CURRENT", 10),
            {k: v for k, v in _supplier("OTHER", 8).items() if k != "unit_of_measure"},
        ], current_supplier_id="CURRENT")


def test_esg_rejects_invalid_pct_and_identifies_illustrative_scoring():
    engine = ESGTCOEngine(company_id="company-1")
    kwargs = dict(
        entity_id="SUP-1", entity_type="supplier", carbon_per_case=1.0,
        packaging_waste_kg_per_unit=0.3, recyclable_packaging_pct=85,
        water_intensity=2, safety_incidents=0,
        supplier_audit_pass_rate=90, local_sourcing_pct=40,
        on_time_payment_rate=95, import_compliance_rate=97,
    )
    with pytest.raises(ValueError):
        engine.score_esg(**(kwargs | {"import_compliance_rate": 130}))
    assert engine.score_esg(**kwargs).to_dict()["source_data_verified"] is False


def test_complete_tco_calculation_is_marked_unverified_and_needs_review():
    comparison = ESGTCOEngine(company_id="company-1").compare_suppliers(
        "SKU", [_supplier("CURRENT", 10), _supplier("OTHER", 8)],
        current_supplier_id="CURRENT",
    )
    result = comparison.to_dict()
    assert result["recommended_supplier_id"] == "OTHER"
    assert result["status"] == "analysis_only"
    assert result["financial_write_performed"] is False
    assert result["inputs_verified"] is False
    assert result["requires_human_review"] is True
    assert all(row["unit_of_measure"] == "case" and row["currency"] == "USD"
               for row in result["supplier_comparison"])
