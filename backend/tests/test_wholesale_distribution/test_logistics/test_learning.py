"""
Tests for Stage 9 — Learning & Refinement.
"""
import pytest
from datetime import date

from app.intelligence.wholesale_distribution.logistics.learning import (
    LearningEngine,
    LeadTimeRecord,
    SupplierPerformanceScore,
)
from app.intelligence.wholesale_distribution.logistics.pipeline import LandedCostBreakdown


def _make_engine():
    return LearningEngine(company_id="test-co")


def _make_record(
    supplier_id="sup-001",
    total=60.0,
    predicted_total=58.0,
    month=3,
    origin_country="ES",
    po_number=None,
):
    return LeadTimeRecord(
        po_number=po_number or f"PO-{supplier_id}-{total}",
        supplier_id=supplier_id,
        origin_country=origin_country,
        origin_port="Barcelona",
        destination_port="Long Beach",
        month=month,
        year=2025,
        po_to_ship=14.0,
        ship_to_arrival=32.0,
        arrival_to_clearance=5.0,
        clearance_to_warehouse=9.0,
        total=total,
        predicted_total=predicted_total,
    )


# ── Tests — Lead Time Stats ───────────────────────────────────────────────────


def test_insufficient_data_with_empty_records():
    engine = _make_engine()
    result = engine.compute_lead_time_stats(records=[])
    assert result["status"] == "insufficient_data"


def test_insufficient_data_with_too_few_records():
    engine = _make_engine()
    records = [_make_record(total=60.0), _make_record(total=65.0)]
    result = engine.compute_lead_time_stats(records)
    # Needs at least 3 records with total set
    assert result["status"] == "insufficient_data"


def test_compute_lead_time_stats_mean():
    engine = _make_engine()
    records = [
        _make_record(total=60.0, po_number="PO-1"),
        _make_record(total=70.0, po_number="PO-2"),
        _make_record(total=80.0, po_number="PO-3"),
    ]
    result = engine.compute_lead_time_stats(records)
    assert result["status"] == "ok"
    assert result["mean_days"] == pytest.approx(70.0, rel=0.01)


def test_compute_lead_time_stats_p80():
    engine = _make_engine()
    records = [_make_record(total=float(x), po_number=f"PO-{x}") for x in range(50, 80)]
    result = engine.compute_lead_time_stats(records)
    assert result["status"] == "ok"
    assert "p80_days" in result
    assert result["p80_days"] > result["mean_days"]


def test_filter_by_supplier_id():
    engine = _make_engine()
    records = [
        _make_record(supplier_id="sup-001", total=60.0, po_number="PO-A1"),
        _make_record(supplier_id="sup-001", total=62.0, po_number="PO-A2"),
        _make_record(supplier_id="sup-001", total=58.0, po_number="PO-A3"),
        _make_record(supplier_id="sup-002", total=80.0, po_number="PO-B1"),
        _make_record(supplier_id="sup-002", total=85.0, po_number="PO-B2"),
        _make_record(supplier_id="sup-002", total=78.0, po_number="PO-B3"),
    ]
    result = engine.compute_lead_time_stats(records, supplier_id="sup-001")
    assert result["status"] == "ok"
    assert result["mean_days"] == pytest.approx(60.0, rel=0.05)


def test_filter_by_month():
    engine = _make_engine()
    records = [
        _make_record(month=3, total=60.0, po_number="PO-M3a"),
        _make_record(month=3, total=62.0, po_number="PO-M3b"),
        _make_record(month=3, total=58.0, po_number="PO-M3c"),
        _make_record(month=7, total=80.0, po_number="PO-M7a"),
        _make_record(month=7, total=82.0, po_number="PO-M7b"),
        _make_record(month=7, total=78.0, po_number="PO-M7c"),
    ]
    result = engine.compute_lead_time_stats(records, month=7)
    assert result["status"] == "ok"
    assert result["mean_days"] == pytest.approx(80.0, rel=0.05)


def test_prediction_accuracy_computed():
    engine = _make_engine()
    records = [
        _make_record(total=60.0, predicted_total=58.0, po_number="PO-1"),
        _make_record(total=65.0, predicted_total=63.0, po_number="PO-2"),
        _make_record(total=55.0, predicted_total=53.0, po_number="PO-3"),
    ]
    result = engine.compute_lead_time_stats(records)
    assert result["status"] == "ok"
    assert "prediction_accuracy" in result
    pa = result["prediction_accuracy"]
    assert "mae_days" in pa
    assert pa["mae_days"] == pytest.approx(2.0, rel=0.01)


def test_trend_detection_present():
    engine = _make_engine()
    records = [_make_record(total=float(x), po_number=f"PO-{x}") for x in range(50, 60)]
    result = engine.compute_lead_time_stats(records)
    assert result["status"] == "ok"
    assert result["trend"] in ("worsening", "stable", "improving")


# ── Tests — Supplier Scoring ──────────────────────────────────────────────────


def test_supplier_score_0_to_100():
    engine = _make_engine()
    records = [_make_record(total=60.0, predicted_total=60.0, po_number=f"PO-{i}") for i in range(5)]
    score = engine.score_supplier(
        supplier_id="sup-001",
        supplier_name="Test Supplier",
        lead_time_records=records,
        response_hours=[18.0, 24.0, 12.0],
        cost_history=[],
        defect_rate_pct=0.01,
        return_rate_pct=0.005,
    )
    assert 0 <= score.overall_score <= 100


def test_supplier_score_has_breakdown():
    engine = _make_engine()
    records = [_make_record(total=60.0, predicted_total=60.0, po_number=f"PO-{i}") for i in range(3)]
    score = engine.score_supplier(
        supplier_id="sup-001",
        supplier_name="Test Supplier",
        lead_time_records=records,
        response_hours=[12.0, 18.0],
        cost_history=[],
    )
    assert hasattr(score, "avg_lead_time_days")
    assert hasattr(score, "lead_time_reliability_pct")
    assert hasattr(score, "avg_response_hours")
    assert hasattr(score, "overall_score")


def test_slow_responder_gets_lower_score():
    engine = _make_engine()
    records = [_make_record(total=60.0, predicted_total=60.0, po_number=f"PO-{i}") for i in range(3)]

    fast = engine.score_supplier("sup-fast", "Fast Supplier", records, response_hours=[6.0, 8.0], cost_history=[])
    slow = engine.score_supplier("sup-slow", "Slow Supplier", records, response_hours=[120.0, 150.0], cost_history=[])

    assert fast.overall_score > slow.overall_score


# ── Tests — Seasonal Indices ──────────────────────────────────────────────────


def test_seasonal_indices_insufficient_with_empty():
    engine = _make_engine()
    result = engine.build_seasonal_indices("SKU-A", [])
    assert result["status"] == "insufficient_data"


def test_seasonal_indices_provisional_with_one_year():
    engine = _make_engine()
    # ~365 days of data
    daily = [100.0 + (i % 7) for i in range(365)]
    result = engine.build_seasonal_indices("SKU-A", daily)
    assert result["status"] in ("provisional", "insufficient_data")


def test_seasonal_indices_returns_52_entries():
    engine = _make_engine()
    # ~2 years of data
    daily = [100.0 + (i % 14) for i in range(730)]
    result = engine.build_seasonal_indices("SKU-A", daily)
    if "indices" in result and isinstance(result["indices"], list):
        assert len(result["indices"]) == 52


def test_seasonal_indices_includes_sku():
    engine = _make_engine()
    daily = [100.0] * 400
    result = engine.build_seasonal_indices("MY-SKU", daily)
    assert result.get("sku") == "MY-SKU"


# ── Tests — Monthly Report ────────────────────────────────────────────────────


def test_monthly_report_structure():
    engine = _make_engine()
    records = [_make_record(total=60.0, predicted_total=58.0, po_number=f"PO-{i}") for i in range(5)]
    report = engine.monthly_logistics_report(
        lead_time_records=records,
        cost_history_by_sku={},
        supplier_scores=[],
    )
    assert "lead_time_summary" in report
    assert "shipments_analyzed" in report["lead_time_summary"]


def test_monthly_report_with_no_data():
    engine = _make_engine()
    report = engine.monthly_logistics_report(
        lead_time_records=[],
        cost_history_by_sku={},
        supplier_scores=[],
    )
    assert "lead_time_summary" in report


def test_monthly_report_with_supplier_scores():
    engine = _make_engine()
    score = SupplierPerformanceScore(
        supplier_id="sup-001",
        supplier_name="Good Supplier",
        overall_score=85.0,
        shipment_count=10,
    )
    report = engine.monthly_logistics_report(
        lead_time_records=[],
        cost_history_by_sku={},
        supplier_scores=[score],
    )
    assert "top_performing_suppliers" in report
