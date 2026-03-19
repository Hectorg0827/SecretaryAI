"""Tests for inventory monitoring."""
from decimal import Decimal

import pytest

from app.intelligence.inventory_monitor import evaluate_inventory


def test_healthy_inventory():
    result = evaluate_inventory(
        item_id="1",
        product_name="Test Product",
        warehouse_1_qty=100,
        warehouse_2_qty=50,
        weekly_sell_rate=Decimal("10"),
    )
    assert result.status == "healthy"
    assert not result.needs_po


def test_critical_inventory():
    result = evaluate_inventory(
        item_id="2",
        product_name="Critical Product",
        warehouse_1_qty=10,
        warehouse_2_qty=0,
        weekly_sell_rate=Decimal("10"),  # 1 week left
    )
    assert result.status == "critical"
    assert result.needs_po


def test_out_of_stock():
    result = evaluate_inventory(
        item_id="3",
        product_name="Empty Product",
        warehouse_1_qty=0,
        warehouse_2_qty=0,
        weekly_sell_rate=Decimal("10"),
    )
    assert result.status == "out_of_stock"
    assert result.needs_po


def test_no_sell_rate():
    result = evaluate_inventory(
        item_id="4",
        product_name="Slow Mover",
        warehouse_1_qty=50,
        warehouse_2_qty=0,
        weekly_sell_rate=Decimal("0"),
    )
    assert result.weeks_remaining is None
    assert result.status == "healthy"
