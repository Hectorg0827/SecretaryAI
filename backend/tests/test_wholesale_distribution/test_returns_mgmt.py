"""Tests for §25 — Returns Management & Reverse Logistics."""
import pytest
from app.intelligence.wholesale_distribution.returns_mgmt import (
    DispositionDecision,
    ReturnCategory,
    ReturnRecord,
    ReturnsManagementEngine,
)


@pytest.fixture
def engine():
    return ReturnsManagementEngine(company_id="co-test")


def _return(
    return_id: str,
    reason: str,
    category: ReturnCategory | None = None,
    units: int = 10,
    unit_cost: float = 25.0,
    supplier_id: str = "sup-1",
    lot_number: str | None = None,
    po_number: str | None = None,
) -> ReturnRecord:
    return ReturnRecord(
        return_id=return_id,
        customer_id="cust-1",
        supplier_id=supplier_id,
        sku="SKU-001",
        units=units,
        unit_cost=unit_cost,
        return_reason=reason,
        category=category,
        lot_number=lot_number,
        po_number=po_number,
        return_date="2024-05-01",
    )


class TestReturnClassification:
    def test_defective_reason_classified(self, engine):
        r = _return("r1", "Product arrived broken and doesn't work")
        assert engine.classify_return(r) == ReturnCategory.DEFECTIVE

    def test_wrong_item_classified(self, engine):
        r = _return("r2", "Wrong item sent — not what I ordered")
        assert engine.classify_return(r) == ReturnCategory.WRONG_ITEM

    def test_overstock_classified(self, engine):
        r = _return("r3", "Overstock — ordered too many")
        assert engine.classify_return(r) == ReturnCategory.OVERSTOCK

    def test_expired_classified(self, engine):
        r = _return("r4", "Product is expired — best by date has passed")
        assert engine.classify_return(r) == ReturnCategory.EXPIRED

    def test_damaged_transit_classified(self, engine):
        r = _return("r5", "Box arrived crushed — shipping damage from carrier")
        assert engine.classify_return(r) == ReturnCategory.DAMAGED_IN_TRANSIT


class TestDispositionRecommendation:
    def test_expired_is_destroy(self, engine):
        r = _return("r10", "expired product", category=ReturnCategory.EXPIRED)
        assert engine.recommend_disposition(r) == DispositionDecision.DESTROY

    def test_defective_is_vendor_return(self, engine):
        r = _return("r11", "broken", category=ReturnCategory.DEFECTIVE)
        assert engine.recommend_disposition(r) == DispositionDecision.VENDOR_RETURN

    def test_overstock_recent_is_restock(self, engine):
        r = _return("r12", "overstock", category=ReturnCategory.OVERSTOCK)
        assert engine.recommend_disposition(r, days_since_sale=30) == DispositionDecision.RESTOCK

    def test_overstock_old_is_liquidate(self, engine):
        r = _return("r13", "overstock", category=ReturnCategory.OVERSTOCK)
        assert engine.recommend_disposition(r, days_since_sale=200) == DispositionDecision.LIQUIDATE

    def test_wrong_item_is_restock(self, engine):
        r = _return("r14", "wrong item", category=ReturnCategory.WRONG_ITEM)
        assert engine.recommend_disposition(r) == DispositionDecision.RESTOCK

    def test_food_overstock_is_destroy(self, engine):
        r = _return("r15", "overstock", category=ReturnCategory.OVERSTOCK)
        assert engine.recommend_disposition(r, product_type="food") == DispositionDecision.DESTROY


class TestReturnMetrics:
    def test_empty_returns(self, engine):
        metrics = engine.compute_metrics([], total_revenue=1_000_000)
        assert metrics.total_returns == 0
        assert metrics.total_return_value == 0.0

    def test_return_rate_computed(self, engine):
        returns = [
            _return("r20", "defective product", category=ReturnCategory.DEFECTIVE, units=10, unit_cost=50),
        ]
        metrics = engine.compute_metrics(returns, total_revenue=100_000)
        assert metrics.return_rate_pct == pytest.approx(0.5, abs=0.01)   # $500 / $100k

    def test_by_category_populated(self, engine):
        returns = [
            _return("r30", "overstock", category=ReturnCategory.OVERSTOCK),
            _return("r31", "defective", category=ReturnCategory.DEFECTIVE),
            _return("r32", "overstock", category=ReturnCategory.OVERSTOCK),
        ]
        metrics = engine.compute_metrics(returns, total_revenue=50_000)
        assert "overstock" in metrics.by_category
        assert metrics.by_category["overstock"]["count"] == 2

    def test_financial_impact_computed(self, engine):
        returns = [_return(f"r{i}", "overstock", category=ReturnCategory.OVERSTOCK) for i in range(5)]
        metrics = engine.compute_metrics(returns, total_revenue=100_000)
        fi = metrics.financial_impact
        assert fi["gross_return_exposure"] > 0
        assert fi["estimated_net_cost"] > 0


class TestSupplierQualityClaim:
    def test_claim_generated_above_threshold(self, engine):
        returns = [
            _return(
                f"r{i}", "defective product",
                category=ReturnCategory.DEFECTIVE,
                units=5,
                lot_number="LOT-001",
                po_number="PO-001",
            )
            for i in range(10)
        ]
        # 50 defective units out of 500 received = 10% > 1% threshold
        claim = engine.generate_supplier_claim(
            supplier_id="sup-1",
            supplier_name="ABC Supplier",
            returns=returns,
            total_units_received=500,
            period_start="2024-04-01",
            period_end="2024-05-31",
            agreed_defect_threshold_pct=1.0,
        )
        assert claim is not None
        assert claim.defect_rate_pct > 1.0
        assert claim.claim_amount > 0
        assert "ABC Supplier" in claim.narrative

    def test_no_claim_below_threshold(self, engine):
        returns = [
            _return("r100", "defective", category=ReturnCategory.DEFECTIVE, units=1)
        ]
        # 1 unit out of 1000 = 0.1% < 1% threshold
        claim = engine.generate_supplier_claim(
            supplier_id="sup-1",
            supplier_name="ABC Supplier",
            returns=returns,
            total_units_received=1000,
            period_start="2024-04-01",
            period_end="2024-05-31",
            agreed_defect_threshold_pct=1.0,
        )
        assert claim is None

    def test_claim_includes_po_and_lot_numbers(self, engine):
        returns = [
            _return("r200", "defective", category=ReturnCategory.DEFECTIVE,
                    units=20, lot_number="LOT-A", po_number="PO-100"),
            _return("r201", "defective", category=ReturnCategory.DEFECTIVE,
                    units=20, lot_number="LOT-B", po_number="PO-101"),
        ]
        claim = engine.generate_supplier_claim(
            supplier_id="sup-1",
            supplier_name="Test Supplier",
            returns=returns,
            total_units_received=100,
            period_start="2024-01-01",
            period_end="2024-06-30",
            agreed_defect_threshold_pct=1.0,
        )
        assert claim is not None
        assert "LOT-A" in claim.lot_numbers or "LOT-B" in claim.lot_numbers
        assert "PO-100" in claim.po_numbers or "PO-101" in claim.po_numbers
