"""
Tests for Stage 1 — Demand Monitoring & Reorder Intelligence.
"""
import pytest
from app.intelligence.wholesale_distribution.logistics.reorder_engine import ReorderEngine
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    SKUProfile,
    SupplyChainType,
    UrgencyLevel,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

DAILY_SALES_90 = [10.0] * 90          # 10 cases/day, no variability
LEAD_TIMES_OVERSEAS = [56.0, 60.0, 63.0, 55.0, 58.0, 61.0]  # ~6–9 weeks


def _make_profile(
    sku="WINE-001",
    supply_chain_type=SupplyChainType.OVERSEAS,
    stock_on_hand=200,
    stock_in_transit=0,
):
    return SKUProfile(
        sku=sku,
        name="Test Wine",
        supplier_id="sup-001",
        supplier_name="Bodegas Test S.A.",
        supply_chain_type=supply_chain_type,
        origin_country="ES",
        unit_cost_fob=25.0,
        cases_per_pallet=50,
        cases_per_40hc_container=1000,
        stock_on_hand=stock_on_hand,
        stock_committed=0,
        stock_in_transit=stock_in_transit,
    )


def _make_engine():
    return ReorderEngine(company_id="test-co")


# ── Tests — Basic Evaluation ──────────────────────────────────────────────────


def test_returns_none_when_stock_comfortable():
    """No reorder needed when stock is well above ROP."""
    engine = _make_engine()
    # 10 cases/day, lead time ~60 days → need ~600 cases; stock=2000 → comfortable
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=2000),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result is None


def test_red_urgency_when_stock_critical():
    """RED urgency when stock is below lead time × 0.9."""
    engine = _make_engine()
    # 10 cases/day, lead time p80 ≈ 63 days → RED threshold ≈ 567 cases
    # Stock = 200 → deep in RED territory
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result is not None
    assert result.urgency == UrgencyLevel.RED


def test_yellow_urgency_intermediate_stock():
    """YELLOW urgency between RED and comfortable thresholds."""
    engine = _make_engine()
    # ROP ≈ 630 + safety_stock, YELLOW threshold ≈ 787 cases
    # stock = 750 should land in YELLOW or RED
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=750),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result is not None
    assert result.urgency in (UrgencyLevel.RED, UrgencyLevel.YELLOW)


def test_days_of_supply_calculation():
    """days_of_supply should approximate stock / daily_demand."""
    engine = _make_engine()
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result is not None
    # 200 cases / ~10 cases per day = ~20 days
    assert 15 <= result.days_of_supply <= 25


def test_in_transit_stock_reduces_urgency():
    """Stock in transit counts toward net available."""
    engine = _make_engine()
    # Without transit: RED. With 500 cases in transit: should be YELLOW or None
    result_no_transit = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200, stock_in_transit=0),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    result_with_transit = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200, stock_in_transit=600),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result_no_transit is not None
    assert result_no_transit.urgency == UrgencyLevel.RED
    # With enough transit stock, urgency should be lower or None
    if result_with_transit is not None:
        assert result_with_transit.urgency != UrgencyLevel.RED


def test_recommended_qty_rounded_to_pallet():
    """Recommended quantity must be a multiple of cases_per_pallet (50)."""
    engine = _make_engine()
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result is not None
    assert result.recommended_qty % 50 == 0


def test_container_fill_computed_for_overseas():
    """Container fill % should be set for OVERSEAS SKUs."""
    engine = _make_engine()
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200, supply_chain_type=SupplyChainType.OVERSEAS),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
    )
    assert result is not None
    assert result.container_fill_pct is not None
    assert 0 <= result.container_fill_pct <= 200  # could be multi-container


def test_price_change_flag_set_when_fob_increases():
    """Price change flag fires when last_fob_price differs from profile.unit_cost_fob."""
    engine = _make_engine()
    # profile.unit_cost_fob = 25.0; last_fob_price = 24.0 → price changed
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200),   # unit_cost_fob=25.0
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
        last_fob_price=24.00,
    )
    assert result is not None
    assert result.price_change_flag is True


def test_no_price_flag_when_prices_equal():
    """No price flag when last_fob_price equals profile.unit_cost_fob."""
    engine = _make_engine()
    # profile.unit_cost_fob = 25.0; last_fob_price = 25.0 → no change
    result = engine.evaluate_sku(
        profile=_make_profile(stock_on_hand=200),   # unit_cost_fob=25.0
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=LEAD_TIMES_OVERSEAS,
        last_fob_price=25.00,
    )
    assert result is not None
    assert not result.price_change_flag


# ── Tests — Build Reorder Queue ───────────────────────────────────────────────


def test_build_reorder_queue_sorted_red_first():
    """RED urgency SKUs must come before YELLOW in the output queue."""
    engine = _make_engine()
    sku_data = [
        {
            "profile": _make_profile(sku="A", stock_on_hand=750),
            "daily_sales_history": DAILY_SALES_90,
            "lead_time_history": LEAD_TIMES_OVERSEAS,
        },
        {
            "profile": _make_profile(sku="B", stock_on_hand=200),
            "daily_sales_history": DAILY_SALES_90,
            "lead_time_history": LEAD_TIMES_OVERSEAS,
        },
    ]
    queue = engine.build_reorder_queue(sku_data)
    urgencies = [p.urgency for p in queue]

    # All REDs must precede any YELLOW
    found_yellow = False
    for u in urgencies:
        if u == UrgencyLevel.YELLOW:
            found_yellow = True
        if found_yellow:
            assert u != UrgencyLevel.RED, "RED appeared after YELLOW in queue"


def test_build_reorder_queue_excludes_comfortable_skus():
    """SKUs with comfortable stock must not appear in the queue."""
    engine = _make_engine()
    sku_data = [
        {
            "profile": _make_profile(sku="COMFORTABLE", stock_on_hand=5000),
            "daily_sales_history": DAILY_SALES_90,
            "lead_time_history": LEAD_TIMES_OVERSEAS,
        },
    ]
    queue = engine.build_reorder_queue(sku_data)
    assert len(queue) == 0


def test_build_reorder_queue_empty_input():
    engine = _make_engine()
    assert engine.build_reorder_queue([]) == []


def test_domestic_sku_uses_shorter_thresholds():
    """Domestic SKUs should have lower lead time (days) driving lower urgency thresholds."""
    engine = _make_engine()
    domestic_lead_times = [3.0, 4.0, 5.0, 3.0, 4.0]
    # 10 cases/day, 3-5 day lead time — need only 30-50 cases buffer
    # 500 cases on hand should be comfortable for a domestic supplier
    result = engine.evaluate_sku(
        profile=_make_profile(
            stock_on_hand=500,
            supply_chain_type=SupplyChainType.DOMESTIC,
        ),
        daily_sales_history=DAILY_SALES_90,
        lead_time_history=domestic_lead_times,
    )
    # 500 cases / 10 per day = 50 days; ROP for 4-day lead time is ~40 cases → comfortable
    assert result is None
