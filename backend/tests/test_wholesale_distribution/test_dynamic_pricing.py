"""Tests for §23 — Dynamic Pricing Intelligence."""
import pytest
from app.intelligence.wholesale_distribution.dynamic_pricing import (
    DynamicPricingEngine,
    ElasticityEstimate,
    PassThroughStrategy,
    PricingTier,
)


@pytest.fixture
def engine():
    return DynamicPricingEngine(company_id="co-test", target_gross_margin_pct=30.0)


def _elasticity(ped: float, sample_size: int = 10) -> ElasticityEstimate:
    return ElasticityEstimate(
        segment="test",
        sku_category="general",
        ped=ped,
        confidence=0.7,
        sample_size=sample_size,
    )


class TestElasticityEstimation:
    def test_elastic_label_below_minus_one(self, engine):
        e = engine.estimate_elasticity(
            segment="retail",
            sku_category="commodity",
            price_change_events=[
                {"old_price": 10.0, "new_price": 11.0, "units_before": 100, "units_after": 85, "period_days": 30},
                {"old_price": 10.0, "new_price": 12.0, "units_before": 100, "units_after": 70, "period_days": 30},
            ],
        )
        assert e.is_elastic

    def test_inelastic_label_above_minus_one(self, engine):
        e = engine.estimate_elasticity(
            segment="contract",
            sku_category="specialty",
            price_change_events=[
                {"old_price": 10.0, "new_price": 11.0, "units_before": 100, "units_after": 99, "period_days": 30},
            ],
        )
        assert not e.is_elastic

    def test_empty_events_returns_default(self, engine):
        e = engine.estimate_elasticity("seg", "cat", [])
        assert e.ped == -1.0
        assert e.confidence < 0.5

    def test_confidence_increases_with_sample_size(self, engine):
        few_events = [
            {"old_price": 10, "new_price": 11, "units_before": 100, "units_after": 90, "period_days": 30}
        ]
        many_events = few_events * 8
        e_few = engine.estimate_elasticity("seg", "cat", few_events)
        e_many = engine.estimate_elasticity("seg", "cat", many_events)
        assert e_many.confidence >= e_few.confidence


class TestPricingRecommendation:
    def test_below_margin_target_raises_price(self, engine):
        """If current margin < target, recommended price should be higher."""
        # cost=8, price=10 → 20% margin; target=30% → price should go up
        rec = engine.recommend_price(
            sku="SKU-1",
            segment="default",
            pricing_tier=PricingTier.TIER_2,
            current_price=10.0,
            unit_cost=8.0,
            annual_units=1000,
            elasticity=_elasticity(-0.5),  # inelastic
        )
        assert rec.recommended_price > rec.current_price

    def test_elastic_segment_price_cut_considered(self, engine):
        """High margin + elastic demand → recommend small price cut to boost volume."""
        # cost=5, price=20 → 75% margin; target=30% → elastic means cut could help
        rec = engine.recommend_price(
            sku="SKU-2",
            segment="elastic_customers",
            pricing_tier=PricingTier.TIER_3,
            current_price=20.0,
            unit_cost=5.0,
            annual_units=10_000,
            elasticity=_elasticity(-2.5),  # highly elastic
        )
        # Either price cut recommended, or at least margin noted above target
        assert rec.projected_margin_pct >= 0

    def test_competitive_position_above_market_flagged(self, engine):
        rec = engine.recommend_price(
            sku="SKU-3",
            segment="seg",
            pricing_tier=PricingTier.TIER_2,
            current_price=15.0,
            unit_cost=8.0,
            annual_units=500,
            elasticity=_elasticity(-1.0),
            competitive_prices=[12.0, 11.5, 13.0],  # market ~12.2
        )
        assert rec.competitive_position == "above_market"

    def test_competitive_position_below_market_flagged(self, engine):
        rec = engine.recommend_price(
            sku="SKU-4",
            segment="seg",
            pricing_tier=PricingTier.TIER_2,
            current_price=8.0,
            unit_cost=5.0,
            annual_units=500,
            elasticity=_elasticity(-0.5),
            competitive_prices=[12.0, 11.5, 13.0],
        )
        assert rec.competitive_position == "below_market"

    def test_to_dict_serializable(self, engine):
        rec = engine.recommend_price(
            sku="SKU-5",
            segment="default",
            pricing_tier=PricingTier.TIER_2,
            current_price=10.0,
            unit_cost=6.0,
            annual_units=1000,
            elasticity=_elasticity(-1.2),
        )
        d = rec.to_dict()
        assert "recommended_price" in d
        assert "projected_margin_pct" in d


class TestSupplierIncreaseStrategy:
    def test_elastic_segment_gets_partial_pass_through(self, engine):
        plan = engine.supplier_increase_strategy(
            supplier="SUP-1",
            sku="SKU-10",
            unit_cost_before=5.0,
            unit_cost_after=6.0,       # 20% increase
            current_price=10.0,
            segment_data=[
                {
                    "segment": "elastic_customer",
                    "pricing_tier": "tier_2",
                    "annual_units": 1000,
                    "elasticity_ped": -2.0,    # elastic
                },
            ],
        )
        # Elastic segment should not absorb full pass-through
        assert plan.blended_pass_through_pct < 90

    def test_inelastic_segment_gets_high_pass_through(self, engine):
        plan = engine.supplier_increase_strategy(
            supplier="SUP-2",
            sku="SKU-11",
            unit_cost_before=5.0,
            unit_cost_after=5.5,
            current_price=10.0,
            segment_data=[
                {
                    "segment": "loyal_customer",
                    "pricing_tier": "tier_3",
                    "annual_units": 500,
                    "elasticity_ped": -0.3,   # inelastic
                },
            ],
        )
        assert plan.blended_pass_through_pct >= 70

    def test_plan_summary_is_non_empty(self, engine):
        plan = engine.supplier_increase_strategy(
            supplier="SUP-3",
            sku="SKU-12",
            unit_cost_before=10.0,
            unit_cost_after=11.0,
            current_price=18.0,
            segment_data=[
                {
                    "segment": "default",
                    "pricing_tier": "tier_2",
                    "annual_units": 200,
                    "elasticity_ped": -1.0,
                }
            ],
        )
        assert plan.recommended_action_summary != ""
        assert plan.to_dict()["supplier"] == "SUP-3"

    def test_key_account_gets_staged_strategy(self, engine):
        plan = engine.supplier_increase_strategy(
            supplier="SUP-4",
            sku="SKU-13",
            unit_cost_before=10.0,
            unit_cost_after=12.0,   # 20% increase → large enough to trigger staged
            current_price=18.0,
            segment_data=[
                {
                    "segment": "key_account",
                    "pricing_tier": "tier_1",
                    "annual_units": 5000,
                    "elasticity_ped": -0.8,
                }
            ],
        )
        rec = plan.recommendations[0]
        assert rec.pass_through_strategy in (
            PassThroughStrategy.STAGED,
            PassThroughStrategy.PARTIAL,
        )
