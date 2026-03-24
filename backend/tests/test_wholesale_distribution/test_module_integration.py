"""Integration tests for WholesaleDistributionModule + IndustryRegistry."""
import pytest
from app.intelligence.industry_registry import (
    get_industry_module,
    list_verticals,
    register_vertical,
)
from app.intelligence.wholesale_distribution.module import WholesaleDistributionModule
from app.intelligence.wholesale_distribution.returns_mgmt import (
    ReturnCategory,
    ReturnRecord,
)


class TestIndustryRegistry:
    def test_wholesale_distribution_is_registered(self):
        verticals = list_verticals()
        ids = [v["vertical_id"] for v in verticals]
        assert "wholesale_distribution" in ids

    def test_get_module_returns_correct_type(self):
        module = get_industry_module("wholesale_distribution", company_id="co-1")
        assert isinstance(module, WholesaleDistributionModule)

    def test_unknown_vertical_falls_back_to_wholesale(self):
        module = get_industry_module("nonexistent_vertical", company_id="co-2")
        assert isinstance(module, WholesaleDistributionModule)

    def test_none_vertical_uses_default(self):
        module = get_industry_module(None, company_id="co-3")
        assert isinstance(module, WholesaleDistributionModule)

    def test_register_custom_vertical(self):
        register_vertical(
            "test_vertical",
            "app.intelligence.wholesale_distribution.module.WholesaleDistributionModule",
        )
        verticals = list_verticals()
        ids = [v["vertical_id"] for v in verticals]
        assert "test_vertical" in ids

    def test_config_passed_to_module(self):
        module = get_industry_module(
            "wholesale_distribution",
            company_id="co-4",
            config={"annual_revenue": 25_000_000, "autonomy_tier": 3},
        )
        assert module.config.annual_revenue == 25_000_000
        assert module.config.autonomy_tier == 3


class TestModuleDescribe:
    def test_describe_returns_vertical_id(self):
        module = WholesaleDistributionModule(company_id="co-5")
        info = module.describe()
        assert info["vertical_id"] == "wholesale_distribution"

    def test_describe_lists_capabilities(self):
        module = WholesaleDistributionModule(company_id="co-5")
        info = module.describe()
        assert "reorder_actions" in info["capabilities"]
        assert "customer_drop_analysis" in info["capabilities"]

    def test_describe_lists_sub_modules(self):
        module = WholesaleDistributionModule(company_id="co-5")
        info = module.describe()
        assert any("§18" in s for s in info["sub_modules"])
        assert any("§19" in s for s in info["sub_modules"])


class TestReorderActions:
    def test_returns_action_for_low_stock(self):
        module = WholesaleDistributionModule(
            company_id="co-10",
            config={"autonomy_tier": 2, "annual_revenue": 5_000_000},
        )
        inventory = [
            {
                "sku": "SKU-001",
                "current_stock": 5,
                "unit_cost": 10.0,
                "daily_demand_history": [10.0] * 90,
                "lead_time_history": [7.0] * 20,
                "supplier_id": "sup-1",
            }
        ]
        actions = module.reorder_actions(inventory)
        assert len(actions) == 1
        assert actions[0]["action"]["type"] == "reorder"

    def test_no_action_for_high_stock(self):
        module = WholesaleDistributionModule(company_id="co-11")
        inventory = [
            {
                "sku": "SKU-002",
                "current_stock": 10_000,
                "unit_cost": 5.0,
                "daily_demand_history": [1.0] * 90,
                "lead_time_history": [3.0] * 20,
            }
        ]
        actions = module.reorder_actions(inventory)
        assert len(actions) == 0

    def test_tier2_disposition_is_draft(self):
        module = WholesaleDistributionModule(
            company_id="co-12",
            config={"autonomy_tier": 2},
        )
        inventory = [
            {
                "sku": "SKU-003",
                "current_stock": 2,
                "unit_cost": 20.0,
                "daily_demand_history": [5.0] * 90,
                "lead_time_history": [7.0] * 20,
            }
        ]
        actions = module.reorder_actions(inventory)
        assert len(actions) >= 1
        assert actions[0]["disposition"] == "draft"


class TestCustomerDropAnalysis:
    def test_returns_analysis_dict(self):
        module = WholesaleDistributionModule(company_id="co-20")
        from datetime import date, timedelta
        history = [
            {"date": (date(2024, 1, 1) + timedelta(days=i * 15)).isoformat(), "amount": 500}
            for i in range(20)
        ]
        result = module.customer_drop_analysis(
            customer_id="cust-1",
            customer_name="Test Customer",
            order_history=history,
            days_since_last_order=60,
        )
        assert "primary_cause" in result
        assert "customer_segment" in result

    def test_quality_event_escalated_to_persuadable(self):
        module = WholesaleDistributionModule(company_id="co-21")
        result = module.customer_drop_analysis(
            customer_id="cust-2",
            customer_name="Quality Issue Customer",
            order_history=[{"date": "2024-01-01", "amount": 500}] * 10,
            days_since_last_order=45,
            recent_returns=[{"date": "2024-05-01", "reason": "defective", "amount": 2000}],
        )
        assert result["customer_segment"] == "persuadable"


class TestReturnsDashboard:
    def test_returns_dashboard_empty(self):
        module = WholesaleDistributionModule(company_id="co-30")
        result = module.returns_dashboard(returns=[], total_revenue=100_000)
        assert result["total_returns"] == 0

    def test_returns_dashboard_classifies_returns(self):
        module = WholesaleDistributionModule(company_id="co-31")
        returns = [
            ReturnRecord(
                return_id="r1",
                customer_id="c1",
                supplier_id="s1",
                sku="SKU-001",
                units=5,
                unit_cost=20.0,
                return_reason="broken and defective",
                return_date="2024-05-01",
            )
        ]
        result = module.returns_dashboard(returns, total_revenue=50_000)
        assert result["total_returns"] == 1
        assert "defective" in result["by_category"]

    def test_supplier_claim_generated(self):
        module = WholesaleDistributionModule(company_id="co-32")
        returns = [
            ReturnRecord(
                return_id=f"r{i}",
                customer_id="c1",
                supplier_id="sup-A",
                sku="SKU-010",
                units=10,
                unit_cost=15.0,
                return_reason="defective product",
                category=ReturnCategory.DEFECTIVE,
                return_date="2024-05-01",
                po_number="PO-500",
                lot_number="LOT-X",
            )
            for i in range(5)
        ]
        claim = module.generate_supplier_claim(
            supplier_id="sup-A",
            supplier_name="ABC Co",
            returns=returns,
            total_units_received=100,
            period_start="2024-04-01",
            period_end="2024-05-31",
        )
        assert claim is not None
        assert claim["claim_amount"] > 0


class TestAccessControl:
    def test_viewer_blocked_on_gross_margin(self):
        module = WholesaleDistributionModule(company_id="co-40")
        allowed, reason = module.check_access("u-1", "viewer", "view", "gross_margin")
        assert not allowed

    def test_manager_allowed_on_gross_margin(self):
        module = WholesaleDistributionModule(company_id="co-41")
        allowed, _ = module.check_access("u-2", "manager", "view", "gross_margin")
        assert allowed

    def test_filter_for_viewer_redacts_restricted(self):
        module = WholesaleDistributionModule(company_id="co-42")
        data = {"product_name": "Widget", "api_key": "secret", "gross_margin": 0.30}
        filtered = module.filter_for_role(data, "viewer")
        assert filtered["product_name"] == "Widget"
        assert filtered["api_key"] == "[REDACTED]"
        assert filtered["gross_margin"] == "[REDACTED]"
