"""Tests for file ingestion parsers and normalizers."""
import csv
import io
import os
import tempfile

import pytest

from app.file_ingestion.parsers import (
    detect_report_type,
    normalize_inventory_report,
    normalize_order_report,
    parse_csv,
)


def _write_csv(rows: list[dict], headers: list[str]) -> str:
    """Write a CSV to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(tmp, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    tmp.close()
    return tmp.name


class TestParseCsv:
    def test_basic_parse(self):
        path = _write_csv(
            [{"Item": "Widget A", "QTY": "100", "SKU": "WA-001"}],
            ["Item", "QTY", "SKU"],
        )
        try:
            rows = parse_csv(path)
            assert len(rows) == 1
            assert rows[0]["Item"] == "Widget A"
            assert rows[0]["QTY"] == "100"
        finally:
            os.unlink(path)

    def test_strips_whitespace_from_headers(self):
        path = _write_csv(
            [{"  Item  ": "Widget B", " QTY ": "50"}],
            ["  Item  ", " QTY "],
        )
        try:
            rows = parse_csv(path)
            assert "Item" in rows[0]
            assert "QTY" in rows[0]
        finally:
            os.unlink(path)

    def test_empty_file_returns_empty_list(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        tmp.write("Header1,Header2\n")
        tmp.close()
        try:
            rows = parse_csv(tmp.name)
            assert rows == []
        finally:
            os.unlink(tmp.name)


class TestNormalizeInventoryReport:
    def test_maps_item_to_product_name(self):
        rows = [{"Item": "Widget A", "QTY": "100", "Location": "Warehouse 1"}]
        result = normalize_inventory_report(rows)
        assert result[0]["product_name"] == "Widget A"

    def test_maps_qty_to_quantity(self):
        rows = [{"Item": "Widget A", "QTY": "50"}]
        result = normalize_inventory_report(rows)
        assert result[0]["quantity"] == "50"

    def test_maps_quantity_variant(self):
        rows = [{"Product Name": "Widget B", "Quantity": "75", "SKU": "WB-001"}]
        result = normalize_inventory_report(rows)
        assert result[0]["product_name"] == "Widget B"
        assert result[0]["quantity"] == "75"
        assert result[0]["sku"] == "WB-001"

    def test_maps_on_hand_variant(self):
        rows = [{"Item": "Widget C", "On Hand": "200"}]
        result = normalize_inventory_report(rows)
        assert result[0]["quantity"] == "200"

    def test_unknown_columns_are_dropped(self):
        rows = [{"Item": "Widget A", "RandomColumn": "X"}]
        result = normalize_inventory_report(rows)
        assert "RandomColumn" not in result[0]

    def test_multiple_rows(self):
        rows = [
            {"Item": "Widget A", "QTY": "100"},
            {"Item": "Widget B", "QTY": "200"},
        ]
        result = normalize_inventory_report(rows)
        assert len(result) == 2
        assert result[1]["product_name"] == "Widget B"


class TestNormalizeOrderReport:
    def test_maps_customer_to_customer_name(self):
        rows = [{"Customer": "Acme Corp", "Date": "2024-01-15", "Total": "1500.00"}]
        result = normalize_order_report(rows)
        assert result[0]["customer_name"] == "Acme Corp"

    def test_maps_date_to_order_date(self):
        rows = [{"Customer": "Acme Corp", "Date": "2024-01-15", "Total": "1500.00"}]
        result = normalize_order_report(rows)
        assert result[0]["order_date"] == "2024-01-15"

    def test_maps_total_to_total_amount(self):
        rows = [{"Customer": "Acme Corp", "Order Date": "2024-01-15", "Order Total": "2000.00"}]
        result = normalize_order_report(rows)
        assert result[0]["total_amount"] == "2000.00"


class TestDetectReportType:
    def test_detects_inventory_from_filename(self):
        assert detect_report_type("inventory_export.csv", []) == "inventory"

    def test_detects_inventory_from_headers(self):
        assert detect_report_type("weekly_data.csv", ["Item", "QTY", "Warehouse"]) == "inventory"

    def test_detects_orders_from_filename(self):
        assert detect_report_type("orders_jan.csv", []) == "orders"

    def test_detects_depletion_from_filename(self):
        assert detect_report_type("depletion_report_2024.csv", []) == "depletion_report"

    def test_detects_customs_from_filename(self):
        assert detect_report_type("customs_entry_dec.csv", []) == "customs"

    def test_unknown_returns_unknown(self):
        assert detect_report_type("random_file.csv", ["ColA", "ColB"]) == "unknown"
