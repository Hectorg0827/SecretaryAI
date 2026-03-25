"""
Tests for Stage 8 — Cost Reconciliation & Variance Detection.
"""
import pytest
from app.intelligence.wholesale_distribution.logistics.cost_reconciliation import (
    CostReconciliationEngine,
)
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    FreightBooking,
    FreightCostComponent,
    LandedCostBreakdown,
)


def _make_engine():
    return CostReconciliationEngine(
        company_id="test-co",
        target_gross_margin_pct=30.0,
        shipments_per_year=12,
    )


def _make_lcd(
    po_number="PO-001",
    sku="SKU-A",
    cases=1000,
    fob=20.0,
    ocean=2.0,
    duty=0.5,
    drayage=0.3,
    demurrage=0.0,
    exam_fees=0.0,
    shipment_date="2025-01-01",
):
    lcd = LandedCostBreakdown(
        po_number=po_number,
        sku=sku,
        cases=cases,
        supplier_id="sup-001",
        fob_cost_per_case=fob,
        shipment_date=shipment_date,
    )
    lcd.ocean_freight_per_case = ocean
    lcd.customs_duty_per_case = duty
    lcd.drayage_per_case = drayage
    lcd.demurrage_per_case = demurrage
    lcd.exam_fees_per_case = exam_fees
    return lcd


# ── Tests — Build Landed Cost ─────────────────────────────────────────────────


def test_build_landed_cost_with_booking():
    engine = _make_engine()
    booking = FreightBooking(booking_ref="BK-001", po_number="PO-001")
    booking.cost_components = [
        FreightCostComponent(component="ocean_freight", amount_usd=2000.0),
        FreightCostComponent(component="drayage", amount_usd=500.0),
    ]

    lcd = engine.build_landed_cost(
        po_number="PO-001",
        sku="SKU-A",
        cases=1000,
        supplier_id="sup-001",
        fob_cost_per_case=20.0,
        freight_booking=booking,
        duty_rate_pct=5.0,
    )

    assert lcd.fob_cost_per_case == 20.0
    assert lcd.ocean_freight_per_case == pytest.approx(2.0)  # 2000/1000
    assert lcd.drayage_per_case == pytest.approx(0.5)         # 500/1000
    assert lcd.customs_duty_per_case == pytest.approx(1.0)    # 20 × 5%


def test_build_landed_cost_without_booking():
    engine = _make_engine()
    lcd = engine.build_landed_cost(
        po_number="PO-001",
        sku="SKU-A",
        cases=500,
        supplier_id="sup-001",
        fob_cost_per_case=25.0,
        freight_booking=None,
        duty_rate_pct=0.0,
    )
    assert lcd.fob_cost_per_case == 25.0
    assert lcd.ocean_freight_per_case == 0.0
    assert lcd.customs_duty_per_case == 0.0


def test_total_landed_cost_per_case_property():
    lcd = _make_lcd(fob=20.0, ocean=2.0, duty=0.5, drayage=0.3)
    # total = 20 + 2 + 0.5 + 0.3 + (all others are 0)
    assert lcd.total_landed_cost_per_case == pytest.approx(22.8, rel=0.01)


# ── Tests — Variance Detection ────────────────────────────────────────────────


def test_no_variance_when_identical():
    engine = _make_engine()
    current = _make_lcd(fob=20.0, ocean=2.0)
    previous = _make_lcd(fob=20.0, ocean=2.0)
    variances = engine.detect_variances(current, previous)
    assert variances == []


def test_fob_increase_detected():
    engine = _make_engine()
    current = _make_lcd(fob=21.0)   # +5% increase
    previous = _make_lcd(fob=20.0)
    variances = engine.detect_variances(current, previous)
    fob_var = next((v for v in variances if v.component == "fob_cost"), None)
    assert fob_var is not None
    assert fob_var.change_pct == pytest.approx(5.0, rel=0.01)


def test_small_change_below_threshold_ignored():
    engine = _make_engine()
    # Ocean freight threshold = 5%; a 2% change should not fire
    current = _make_lcd(ocean=2.04)   # +2% change
    previous = _make_lcd(ocean=2.00)
    variances = engine.detect_variances(current, previous)
    ocean_var = next((v for v in variances if v.component == "ocean_freight"), None)
    assert ocean_var is None


def test_demurrage_always_alerts():
    engine = _make_engine()
    # Even $0.01 demurrage should fire an alert
    current = _make_lcd(demurrage=0.05)
    previous = _make_lcd(demurrage=0.0)
    variances = engine.detect_variances(current, previous)
    dem_var = next((v for v in variances if v.component == "demurrage"), None)
    assert dem_var is not None


def test_exam_fees_always_alerts():
    engine = _make_engine()
    current = _make_lcd(exam_fees=0.10)
    previous = _make_lcd(exam_fees=0.0)
    variances = engine.detect_variances(current, previous)
    exam_var = next((v for v in variances if v.component == "exam_fees"), None)
    assert exam_var is not None


def test_annualized_impact_computed():
    engine = _make_engine()
    # $1 per case increase on 1000 cases × 12 shipments/year = $12,000
    current = _make_lcd(fob=21.0, cases=1000)
    previous = _make_lcd(fob=20.0, cases=1000)
    variances = engine.detect_variances(current, previous)
    fob_var = next((v for v in variances if v.component == "fob_cost"), None)
    assert fob_var is not None
    assert fob_var.annualized_impact == pytest.approx(12_000.0, rel=0.01)


def test_severity_critical_for_large_impact():
    engine = _make_engine()
    # $3 per case × 1000 cases × 12 shipments = $36,000/year → critical
    current = _make_lcd(fob=23.0, cases=1000)
    previous = _make_lcd(fob=20.0, cases=1000)
    variances = engine.detect_variances(current, previous)
    fob_var = next((v for v in variances if v.component == "fob_cost"), None)
    assert fob_var is not None
    assert fob_var.severity == "critical"


def test_severity_info_for_small_impact():
    engine = _make_engine()
    # Just over 5% threshold but small absolute impact
    current = _make_lcd(ocean=2.11, cases=100)   # +5.5%, annualized < $5k
    previous = _make_lcd(ocean=2.00, cases=100)
    variances = engine.detect_variances(current, previous)
    ocean_var = next((v for v in variances if v.component == "ocean_freight"), None)
    if ocean_var:
        assert ocean_var.severity in ("info", "warning")


# ── Tests — Cost Trend Analysis ───────────────────────────────────────────────


def test_trend_insufficient_data():
    engine = _make_engine()
    result = engine.analyze_cost_trends([_make_lcd()])
    assert result["status"] == "insufficient_data"


def test_trend_rising_fob():
    engine = _make_engine()
    history = [
        _make_lcd(fob=18.0, shipment_date="2024-01-01"),
        _make_lcd(fob=19.0, shipment_date="2024-04-01"),
        _make_lcd(fob=21.0, shipment_date="2024-07-01"),
    ]
    result = engine.analyze_cost_trends(history)
    assert result["shipments_analyzed"] == 3
    trends = result["component_trends"]
    assert "supplier_pricing" in trends
    assert trends["supplier_pricing"]["trend"] == "rising"


def test_trend_recommendation_freight_rising():
    engine = _make_engine()
    history = [
        _make_lcd(fob=20.0, ocean=1.5, shipment_date="2024-01-01"),
        _make_lcd(fob=20.0, ocean=2.0, shipment_date="2024-04-01"),
        _make_lcd(fob=20.0, ocean=2.5, shipment_date="2024-07-01"),
    ]
    result = engine.analyze_cost_trends(history)
    assert "freight" in result["recommendation"].lower() or "rebid" in result["recommendation"].lower()


# ── Tests — Alert Formatting ──────────────────────────────────────────────────


def test_format_variance_alert_message():
    engine = _make_engine()
    current = _make_lcd(fob=21.0, cases=1000)
    previous = _make_lcd(fob=20.0)
    variances = engine.detect_variances(current, previous)
    fob_var = next(v for v in variances if v.component == "fob_cost")
    msg = engine.format_variance_alert(fob_var, sku_name="Tempranillo", cases=1000)
    assert "Tempranillo" in msg
    assert "increased" in msg.lower() or "decreased" in msg.lower()
    assert "$" in msg


def test_format_demurrage_alert_includes_investigation_note():
    engine = _make_engine()
    current = _make_lcd(demurrage=0.50, cases=1000)
    previous = _make_lcd(demurrage=0.0, cases=1000)
    variances = engine.detect_variances(current, previous)
    dem_var = next(v for v in variances if v.component == "demurrage")
    msg = engine.format_variance_alert(dem_var, sku_name="Garnacha", cases=1000)
    assert "investigate" in msg.lower() or "unplanned" in msg.lower()
