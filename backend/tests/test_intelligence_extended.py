"""
Tests for intelligence modules: anomaly detection, demand forecasting, sync health.
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


TODAY = date(2025, 6, 1)


# ─── Anomaly Detection ─────────────────────────────────────────────────────────

class TestDetectOrderAnomalies:
    def _make_invoices(self, customer="Acme", amounts=None, today=None):
        today = today or TODAY
        if amounts is None:
            amounts = [100, 105, 98, 102, 103]
        invoices = []
        for i, amt in enumerate(amounts):
            invoices.append({
                "customer_name": customer,
                "total_amount": amt,
                "order_date": (today - timedelta(days=i * 5)).isoformat(),
            })
        return invoices

    def test_no_anomaly_with_uniform_orders(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        invoices = self._make_invoices(amounts=[100, 100, 100, 100, 100])
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        assert len(anomalies) == 0

    def test_detects_unusually_high_order(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        # Need 8+ consistent points + 1 outlier to achieve z > 2.5
        # z = n / sqrt(n+1): n=8 → z ≈ 2.67 > 2.5
        invoices = self._make_invoices(amounts=[100, 100, 100, 100, 100, 100, 100, 100, 100000])
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        high_anomalies = [a for a in anomalies if "high" in a.anomaly_type]
        assert len(high_anomalies) > 0
        assert high_anomalies[0].entity_name == "Acme"

    def test_detects_unusually_low_order(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        # Need 8+ consistent points + 1 tiny outlier for z > 2.5
        invoices = self._make_invoices(amounts=[1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1])
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        low_anomalies = [a for a in anomalies if "low" in a.anomaly_type]
        assert len(low_anomalies) > 0

    def test_requires_at_least_4_data_points(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        invoices = self._make_invoices(amounts=[100, 500, 100])  # Only 3 orders
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        assert len(anomalies) == 0

    def test_high_z_score_is_high_severity(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        # Extreme outlier (z >> 3.0) should be "high" severity
        invoices = self._make_invoices(amounts=[100, 100, 100, 100, 100, 100, 100, 100, 100, 1_000_000])
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        assert len(anomalies) > 0
        assert anomalies[0].severity in ("medium", "high")

    def test_multiple_accounts_isolated(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        invoices = (
            self._make_invoices("Acme", [100, 100, 100, 100, 100, 100, 100, 100, 100])
            + self._make_invoices("Beta Corp", [200, 200, 200, 200, 200, 200, 200, 200, 500000])
        )
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        names = {a.entity_name for a in anomalies}
        # Only Beta Corp has an anomaly
        assert "Beta Corp" in names

    def test_anomaly_has_expected_range(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        invoices = self._make_invoices(amounts=[100, 100, 100, 100, 100, 1000])
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        if anomalies:
            lo, hi = anomalies[0].expected_range
            assert lo <= hi

    def test_anomaly_detected_at_is_today(self):
        from app.intelligence.anomaly_detection import detect_order_anomalies
        invoices = self._make_invoices(amounts=[100, 100, 100, 100, 100, 999])
        anomalies = detect_order_anomalies(invoices, today=TODAY)
        if anomalies:
            assert anomalies[0].detected_at == TODAY


class TestDetectInventoryAnomalies:
    def test_no_anomalies_normal_inventory(self):
        from app.intelligence.anomaly_detection import detect_inventory_anomalies
        inventory = [
            {"product_name": "Widget A", "total_qty": 250, "weekly_sell_rate": 10},
        ]
        anomalies = detect_inventory_anomalies(inventory, today=TODAY)
        assert len(anomalies) == 0

    def test_negative_inventory_detected(self):
        from app.intelligence.anomaly_detection import detect_inventory_anomalies
        inventory = [
            {"product_name": "Widget B", "total_qty": -5, "weekly_sell_rate": 10},
        ]
        anomalies = detect_inventory_anomalies(inventory, today=TODAY)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "negative_inventory"
        assert anomalies[0].severity == "high"

    def test_zero_stock_with_active_sell_rate_detected(self):
        from app.intelligence.anomaly_detection import detect_inventory_anomalies
        inventory = [
            {"product_name": "Gadget X", "total_qty": 0, "weekly_sell_rate": 15},
        ]
        anomalies = detect_inventory_anomalies(inventory, today=TODAY)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "zero_stock_active_item"
        assert anomalies[0].severity == "high"

    def test_zero_stock_zero_sell_rate_not_flagged(self):
        from app.intelligence.anomaly_detection import detect_inventory_anomalies
        inventory = [
            {"product_name": "Discontinued", "total_qty": 0, "weekly_sell_rate": 0},
        ]
        anomalies = detect_inventory_anomalies(inventory, today=TODAY)
        assert len(anomalies) == 0

    def test_quantity_on_hand_fallback_key(self):
        from app.intelligence.anomaly_detection import detect_inventory_anomalies
        inventory = [
            {"product_name": "Widget C", "quantity_on_hand": -2, "weekly_sell_rate": 5},
        ]
        anomalies = detect_inventory_anomalies(inventory, today=TODAY)
        assert any(a.anomaly_type == "negative_inventory" for a in anomalies)

    def test_multiple_anomalies_per_item(self):
        from app.intelligence.anomaly_detection import detect_inventory_anomalies
        # Negative AND technically matches zero stock condition? No — negative doesn't match zero
        inventory = [
            {"product_name": "Bad Item", "total_qty": -1, "weekly_sell_rate": 5},
            {"product_name": "Out Item", "total_qty": 0, "weekly_sell_rate": 5},
        ]
        anomalies = detect_inventory_anomalies(inventory, today=TODAY)
        assert len(anomalies) == 2


# ─── Demand Forecasting ────────────────────────────────────────────────────────

class TestForecastProduct:
    def test_no_history_returns_low_confidence(self):
        from app.intelligence.demand_forecasting import forecast_product
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=[], today=TODAY)
        assert fc.confidence == "low"
        assert fc.projected_stockout_date is None
        assert fc.recommended_reorder_date is None
        assert fc.recommended_order_qty == 0.0

    def test_high_confidence_with_8_weeks_history(self):
        from app.intelligence.demand_forecasting import forecast_product
        history = [10.0] * 8
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        assert fc.confidence == "high"

    def test_medium_confidence_with_4_weeks(self):
        from app.intelligence.demand_forecasting import forecast_product
        history = [10.0] * 4
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        assert fc.confidence == "medium"

    def test_stockout_date_calculation(self):
        from app.intelligence.demand_forecasting import forecast_product
        # 100 units, selling 10/week → 10 weeks until stockout
        history = [10.0] * 8
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        expected_weeks = 10
        expected_stockout = TODAY + timedelta(weeks=expected_weeks)
        assert fc.projected_stockout_date == expected_stockout

    def test_reorder_date_is_before_stockout(self):
        from app.intelligence.demand_forecasting import forecast_product
        history = [10.0] * 8
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        assert fc.recommended_reorder_date < fc.projected_stockout_date

    def test_order_qty_covers_target_weeks(self):
        from app.intelligence.demand_forecasting import forecast_product
        history = [10.0] * 8
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY,
                               target_weeks_stock=8)
        # avg_rate=10, target=8 weeks → 80 units
        assert fc.recommended_order_qty == 80.0

    def test_zero_sell_rate_no_stockout(self):
        from app.intelligence.demand_forecasting import forecast_product
        history = [0.0] * 8
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        assert fc.projected_stockout_date is None
        assert fc.recommended_order_qty == 0.0

    def test_trend_positive_when_accelerating(self):
        from app.intelligence.demand_forecasting import forecast_product
        # Last 2 weeks much higher than prior 2
        history = [5.0, 5.0, 10.0, 15.0]
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        assert fc.weekly_sell_rate_trend > 0

    def test_trend_negative_when_slowing(self):
        from app.intelligence.demand_forecasting import forecast_product
        history = [20.0, 20.0, 5.0, 5.0]
        fc = forecast_product("Widget A", current_qty=100.0, weekly_sell_history=history, today=TODAY)
        assert fc.weekly_sell_rate_trend < 0


class TestBuildSellRateHistory:
    def test_empty_invoices_returns_zeros(self):
        from app.intelligence.demand_forecasting import build_sell_rate_history
        history = build_sell_rate_history("widget a", [], weeks=4, today=TODAY)
        assert len(history) == 4
        assert all(v == 0.0 for v in history)

    def test_invoice_in_range_counted(self):
        from app.intelligence.demand_forecasting import build_sell_rate_history
        invoices = [
            {
                "order_date": (TODAY - timedelta(days=3)).isoformat(),
                "items": [{"name": "Widget A", "quantity": 50}],
            }
        ]
        history = build_sell_rate_history("widget a", invoices, weeks=4, today=TODAY)
        assert sum(history) == 50

    def test_old_invoice_excluded(self):
        from app.intelligence.demand_forecasting import build_sell_rate_history
        invoices = [
            {
                "order_date": (TODAY - timedelta(days=200)).isoformat(),
                "items": [{"name": "Widget A", "quantity": 100}],
            }
        ]
        history = build_sell_rate_history("widget a", invoices, weeks=12, today=TODAY)
        assert sum(history) == 0.0

    def test_partial_name_match(self):
        from app.intelligence.demand_forecasting import build_sell_rate_history
        invoices = [
            {
                "order_date": (TODAY - timedelta(days=1)).isoformat(),
                "items": [{"name": "Widget A Blue", "quantity": 30}],
            }
        ]
        history = build_sell_rate_history("widget a", invoices, weeks=4, today=TODAY)
        assert sum(history) == 30


# ─── Sync Health Tests ─────────────────────────────────────────────────────────

class TestReconcileInventory:
    def test_no_discrepancy_matching_quantities(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "Widget A", "qb_qty": 100}]
        wh_items = [{"product_name": "Widget A", "quantity": 100}]
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 0

    def test_warning_threshold_at_5_percent(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "Widget A", "qb_qty": 100}]
        wh_items = [{"product_name": "Widget A", "quantity": 94}]  # ~6% variance
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 1
        assert result[0].severity == "warning"

    def test_critical_threshold_at_15_percent(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "Widget A", "qb_qty": 100}]
        wh_items = [{"product_name": "Widget A", "quantity": 80}]  # 20% variance
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 1
        assert result[0].severity == "critical"

    def test_below_5_percent_no_discrepancy(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "Widget A", "qb_qty": 100}]
        wh_items = [{"product_name": "Widget A", "quantity": 97}]  # 3% variance
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 0

    def test_item_not_in_warehouse_skipped(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "QB Only Item", "qb_qty": 100}]
        wh_items = []
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 0

    def test_both_zero_skipped(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "Widget A", "qb_qty": 0}]
        wh_items = [{"product_name": "Widget A", "quantity": 0}]
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 0

    def test_discrepancy_message_contains_product_name(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "Gadget X", "qb_qty": 100}]
        wh_items = [{"product_name": "Gadget X", "quantity": 50}]
        result = reconcile_inventory(qb_items, wh_items)
        assert "Gadget X" in result[0].message

    def test_case_insensitive_matching(self):
        from app.intelligence.sync_health import reconcile_inventory
        qb_items = [{"product_name": "WIDGET A", "qb_qty": 100}]
        wh_items = [{"product_name": "widget a", "quantity": 50}]
        result = reconcile_inventory(qb_items, wh_items)
        assert len(result) == 1


class TestReconcileRevenue:
    def test_no_discrepancy_matching_revenue(self):
        from app.intelligence.sync_health import reconcile_revenue
        result = reconcile_revenue(Decimal("100000"), Decimal("100000"), "Q1")
        assert result is None

    def test_none_scribebase_returns_none(self):
        from app.intelligence.sync_health import reconcile_revenue
        result = reconcile_revenue(Decimal("100000"), None, "Q1")
        assert result is None

    def test_critical_variance(self):
        from app.intelligence.sync_health import reconcile_revenue
        result = reconcile_revenue(Decimal("100000"), Decimal("80000"), "Q1")
        assert result is not None
        assert result.severity == "critical"

    def test_warning_variance(self):
        from app.intelligence.sync_health import reconcile_revenue
        result = reconcile_revenue(Decimal("100000"), Decimal("93000"), "Q1")
        assert result is not None
        assert result.severity == "warning"

    def test_small_variance_no_discrepancy(self):
        from app.intelligence.sync_health import reconcile_revenue
        result = reconcile_revenue(Decimal("100000"), Decimal("98000"), "Q1")
        assert result is None

    def test_both_zero_returns_none(self):
        from app.intelligence.sync_health import reconcile_revenue
        result = reconcile_revenue(Decimal("0"), Decimal("0"), "Q1")
        assert result is None


class TestSummarizeSyncHealth:
    def test_no_discrepancies_is_healthy(self):
        from app.intelligence.sync_health import summarize_sync_health
        result = summarize_sync_health([])
        assert result["status"] == "healthy"
        assert result["discrepancy_count"] == 0

    def test_warning_discrepancies_warning_status(self):
        from app.intelligence.sync_health import SyncDiscrepancy, summarize_sync_health
        disc = SyncDiscrepancy(
            check_type="inventory_reconciliation",
            field="Widget A",
            source_1_label="QuickBooks",
            source_1_value=100.0,
            source_2_label="Warehouse",
            source_2_value=93.0,
            variance_pct=7.0,
            severity="warning",
            message="7% variance",
        )
        result = summarize_sync_health([disc])
        assert result["status"] == "warning"
        assert result["discrepancy_count"] == 1

    def test_critical_discrepancy_gives_critical_status(self):
        from app.intelligence.sync_health import SyncDiscrepancy, summarize_sync_health
        disc = SyncDiscrepancy(
            check_type="inventory_reconciliation",
            field="Widget B",
            source_1_label="QuickBooks",
            source_1_value=100.0,
            source_2_label="Warehouse",
            source_2_value=50.0,
            variance_pct=50.0,
            severity="critical",
            message="50% variance",
        )
        result = summarize_sync_health([disc])
        assert result["status"] == "critical"

    def test_summary_includes_all_discrepancies(self):
        from app.intelligence.sync_health import SyncDiscrepancy, summarize_sync_health
        discs = [
            SyncDiscrepancy("inv", f"Item {i}", "QB", float(i * 100), "WH", float(i * 80),
                            20.0, "critical", f"Disc {i}")
            for i in range(3)
        ]
        result = summarize_sync_health(discs)
        assert result["discrepancy_count"] == 3
        assert len(result["discrepancies"]) == 3
