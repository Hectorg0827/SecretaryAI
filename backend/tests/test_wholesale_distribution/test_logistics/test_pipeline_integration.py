"""
End-to-end integration tests for the full 9-stage logistics pipeline
via the top-level LogisticsModule.
"""
import pytest
from app.intelligence.wholesale_distribution.logistics import LogisticsModule
from app.intelligence.wholesale_distribution.logistics.logistics_module import LogisticsConfig
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    FreightBooking,
    FreightCostComponent,
    LandedCostBreakdown,
    SKUProfile,
    SupplyChainType,
    UrgencyLevel,
)
from app.intelligence.wholesale_distribution.logistics.learning import LeadTimeRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_module():
    config = LogisticsConfig(
        service_level_target=0.95,
        target_gross_margin_pct=30.0,
        shipments_per_year=12,
        port_free_time_days=5,
        demurrage_rate_per_day=150.0,
        role_email_map={
            "owner": ["owner@test.com"],
            "logistics_manager": ["logistics@test.com"],
        },
    )
    return LogisticsModule(company_id="test-co", config=config)


def _make_profile(stock_on_hand=200):
    return SKUProfile(
        sku="WINE-001",
        name="Test Wine",
        supplier_id="sup-001",
        supplier_name="Bodegas Test S.A.",
        supply_chain_type=SupplyChainType.OVERSEAS,
        origin_country="ES",
        unit_cost_fob=20.0,
        cases_per_pallet=50,
        cases_per_40hc_container=1000,
        stock_on_hand=stock_on_hand,
        stock_committed=0,
        stock_in_transit=0,
    )


DAILY_SALES = [10.0] * 90
LEAD_TIMES = [56.0, 60.0, 63.0, 55.0, 58.0, 61.0]


# ── Tests — Module Instantiation ──────────────────────────────────────────────


def test_module_instantiates():
    lm = _make_module()
    assert lm.company_id == "test-co"


def test_describe_returns_9_stages():
    lm = _make_module()
    desc = lm.describe()
    assert len(desc["pipeline_stages"]) == 9


def test_describe_includes_config():
    lm = _make_module()
    desc = lm.describe()
    assert "target_gross_margin_pct" in desc["config"]
    assert desc["config"]["target_gross_margin_pct"] == 30.0


# ── Tests — Stage 1: Reorder Queue ───────────────────────────────────────────


def test_stage1_red_sku_detected():
    lm = _make_module()
    sku_data = [{
        "profile": _make_profile(stock_on_hand=200),  # critically low
        "daily_sales_history": DAILY_SALES,
        "lead_time_history": LEAD_TIMES,
    }]
    results = lm.evaluate_reorder_queue(sku_data)
    assert len(results) == 1
    assert results[0]["package"].urgency == UrgencyLevel.RED


def test_stage1_comfortable_sku_not_returned():
    lm = _make_module()
    sku_data = [{
        "profile": _make_profile(stock_on_hand=5000),
        "daily_sales_history": DAILY_SALES,
        "lead_time_history": LEAD_TIMES,
    }]
    results = lm.evaluate_reorder_queue(sku_data)
    assert len(results) == 0


def test_stage1_red_alert_generated():
    lm = _make_module()
    sku_data = [{
        "profile": _make_profile(stock_on_hand=200),
        "daily_sales_history": DAILY_SALES,
        "lead_time_history": LEAD_TIMES,
    }]
    results = lm.evaluate_reorder_queue(sku_data)
    assert results[0]["alert"] is not None
    assert results[0]["alert"].severity in ("warning", "critical")


# ── Tests — Stage 4: Freight Invoice Parsing ─────────────────────────────────


def test_stage4_parse_freight_invoice():
    lm = _make_module()
    booking = FreightBooking(booking_ref="BK-001")
    # Invoice text must NOT start with a blank line (parser breaks on first line)
    invoice_text = "Ocean Freight $2,500.00\nFuel Surcharge (BAF) $350.00\nOrigin Charges (THC) $200.00"
    updated = lm.capture_freight_invoice(booking, invoice_text, total_cases=1000)
    summary = lm.freight_cost_summary(updated)
    assert summary["total_cost"] > 0
    assert "ocean_freight" in summary["by_component"]


def test_stage4_manual_freight_cost():
    lm = _make_module()
    booking = FreightBooking(booking_ref="BK-002")
    updated = lm.add_freight_cost_manual(booking, "drayage", 750.0, "LA port to Riverside WH")
    summary = lm.freight_cost_summary(updated)
    assert summary["by_component"]["drayage"] == 750.0


# ── Tests — Stage 6: Customs ──────────────────────────────────────────────────


def test_stage6_log_port_arrival():
    lm = _make_module()
    result = lm.log_port_arrival("BK-001", notes="Arrived at Long Beach")
    assert result["event"].milestone == "arrived_at_port"


def test_stage6_customs_hold_fires_alert():
    lm = _make_module()
    result = lm.log_customs_hold(
        booking_id="BK-001",
        hold_reason="Missing Certificate of Origin",
        days_at_port=3,
    )
    assert result["alert"] is not None
    # Missing COO is a critical document issue
    assert result["alert"].severity in ("critical", "red", "alert")


def test_stage6_demurrage_risk_no_alert_in_free_time():
    lm = _make_module()
    result = lm.check_demurrage_risk(
        booking_id="BK-001",
        arrival_date_str="2025-03-24",
        today_str="2025-03-25",  # 1 day in, free time = 5 days
    )
    assert result["risk"]["severity"] == "info"
    assert result["alert"] is None


def test_stage6_demurrage_alert_when_accruing():
    lm = _make_module()
    result = lm.check_demurrage_risk(
        booking_id="BK-001",
        arrival_date_str="2025-03-15",
        today_str="2025-03-25",  # 10 days → 5 demurrage days
    )
    assert result["risk"]["demurrage_days"] == 5
    assert result["alert"] is not None


def test_stage6_broker_email_parsed():
    lm = _make_module()
    result = lm.parse_broker_email(
        booking_id="BK-001",
        subject="Entry Filed - Container TCKU123456",
        body="Entry has been filed with US Customs for container TCKU123456.",
    )
    assert result["milestone"] == "entry_filed"


# ── Tests — Stage 7: Warehouse Receipt ───────────────────────────────────────


def test_stage7_receipt_processes():
    lm = _make_module()
    booking = FreightBooking(booking_ref="BK-001", po_number="PO-001")
    result = lm.process_warehouse_receipt(
        booking=booking,
        po_number="PO-001",
        line_items_received=[
            {"sku": "WINE-001", "qty_ordered": 500, "qty_received": 500, "condition_notes": ""},
        ],
        received_by="warehouse_staff",
        po_sent_at="2025-01-10",
        supplier_ship_date="2025-01-24",
    )
    assert result["receipt"].total_cases_received == 500
    assert len(result["shortages"]) == 0
    assert result["alert"] is not None


def test_stage7_shortage_detected():
    lm = _make_module()
    booking = FreightBooking(booking_ref="BK-002", po_number="PO-002")
    result = lm.process_warehouse_receipt(
        booking=booking,
        po_number="PO-002",
        line_items_received=[
            {"sku": "WINE-001", "qty_ordered": 500, "qty_received": 450, "condition_notes": ""},
        ],
    )
    assert len(result["shortages"]) == 1
    assert result["shortages"][0]["shortage"] == 50


# ── Tests — Stage 8: Cost Reconciliation ─────────────────────────────────────


def test_stage8_no_variance_identical_shipments():
    lm = _make_module()

    def _lcd(fob):
        lcd = LandedCostBreakdown(
            po_number="PO-001", sku="WINE-001", cases=1000,
            supplier_id="sup-001", fob_cost_per_case=fob, shipment_date="2025-01-01",
        )
        lcd.ocean_freight_per_case = 2.0
        return lcd

    result = lm.reconcile_costs(_lcd(20.0), _lcd(20.0))
    assert len(result["variances"]) == 0


def test_stage8_fob_increase_fires_variance():
    lm = _make_module()

    def _lcd(fob):
        lcd = LandedCostBreakdown(
            po_number="PO-001", sku="WINE-001", cases=1000,
            supplier_id="sup-001", fob_cost_per_case=fob, shipment_date="2025-01-01",
        )
        return lcd

    result = lm.reconcile_costs(_lcd(21.0), _lcd(20.0))
    assert len(result["variances"]) > 0
    assert len(result["formatted"]) > 0
    assert len(result["alerts"]) > 0


# ── Tests — Stage 9: Learning ─────────────────────────────────────────────────


def test_stage9_record_and_retrieve_lead_time():
    lm = _make_module()
    record = LeadTimeRecord(
        po_number="PO-001",
        supplier_id="sup-001",
        origin_country="ES",
        origin_port="Barcelona",
        destination_port="Long Beach",
        month=3,
        year=2025,
        total=62.0,
        predicted_total=60.0,
    )
    lm.record_lead_time(record)
    # One record → insufficient_data (need 3+)
    stats = lm.get_lead_time_stats()
    assert stats.get("status") in ("insufficient_data", "ok")


def test_stage9_monthly_report_generated():
    lm = _make_module()
    report = lm.monthly_logistics_report()
    assert isinstance(report, dict)
    assert "lead_time_summary" in report


def test_stage9_score_supplier_returns_score():
    lm = _make_module()
    score = lm.score_supplier(
        supplier_id="sup-001",
        supplier_name="Test Supplier",
        response_hours_history=[24.0, 18.0, 36.0],
        defect_rate_pct=0.01,
    )
    assert hasattr(score, "overall_score")
    assert 0 <= score.overall_score <= 100
