"""
Tests for Stage 2 — Purchase Order Generation.
"""
import pytest
from app.intelligence.wholesale_distribution.logistics.po_generator import POGenerator
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    ReorderDecisionPackage,
    UrgencyLevel,
    SupplyChainType,
    SKUProfile,
)


def _make_profile(sku, supplier_id="sup-001", supply_chain_type=SupplyChainType.OVERSEAS):
    return SKUProfile(
        sku=sku,
        name=f"Product {sku}",
        supplier_id=supplier_id,
        supplier_name=f"Supplier {supplier_id}",
        supply_chain_type=supply_chain_type,
        origin_country="ES",
        unit_cost_fob=20.0,
        cases_per_pallet=50,
        cases_per_40hc_container=1000,
        stock_on_hand=100,
        stock_committed=0,
        stock_in_transit=0,
    )


def _make_package(sku, supplier_id="sup-001", qty=500, urgency=UrgencyLevel.RED,
                  supply_chain_type=SupplyChainType.OVERSEAS):
    profile = _make_profile(sku, supplier_id, supply_chain_type)
    pkg = ReorderDecisionPackage(
        sku_profile=profile,
        urgency=urgency,
        recommended_qty=qty,
        approved=True,  # must be approved for PO generation
        estimated_fob_cost=20.0 * qty,
        days_of_supply=20.0,
        lead_time_p80_days=60.0,
    )
    return pkg


def _make_generator():
    return POGenerator(
        company_id="test-co",
        ship_to_address="123 Warehouse Blvd, Riverside, CA 92509",
    )


SUPPLIER_CONFIGS = {
    "sup-001": {
        "email": "orders@sup001.com",
        "payment_terms": "Net 30",
        "language": "en",
        "min_order_cases": 100,
    },
    "sup-002": {
        "email": "orders@sup002.com",
        "payment_terms": "Net 60",
        "language": "en",
        "min_order_cases": 50,
    },
}


# ── Tests — PO Generation ─────────────────────────────────────────────────────


def test_generates_one_po_per_supplier():
    gen = _make_generator()
    packages = [
        _make_package("SKU-A", "sup-001", 500),
        _make_package("SKU-B", "sup-001", 300),
        _make_package("SKU-C", "sup-002", 200),
    ]
    pos = gen.generate_pos(packages, SUPPLIER_CONFIGS)
    assert len(pos) == 2  # one per supplier
    supplier_ids = {po.supplier_id for po in pos}
    assert "sup-001" in supplier_ids
    assert "sup-002" in supplier_ids


def test_po_aggregates_cases_for_same_supplier():
    gen = _make_generator()
    packages = [
        _make_package("SKU-A", "sup-001", 500),
        _make_package("SKU-B", "sup-001", 300),
    ]
    pos = gen.generate_pos(packages, SUPPLIER_CONFIGS)
    assert len(pos) == 1
    assert pos[0].total_cases == 800


def test_po_number_generated():
    gen = _make_generator()
    packages = [_make_package("SKU-A", "sup-001", 500)]
    pos = gen.generate_pos(packages, SUPPLIER_CONFIGS)
    assert len(pos[0].po_number) > 3


def test_po_total_value_calculated():
    gen = _make_generator()
    packages = [_make_package("SKU-A", "sup-001", 100)]  # 100 cases × $20 FOB = $2000
    pos = gen.generate_pos(packages, SUPPLIER_CONFIGS)
    assert pos[0].total_value == pytest.approx(2000.0, rel=0.01)


def test_unapproved_packages_skipped():
    gen = _make_generator()
    profile = _make_profile("SKU-SKIP", "sup-001")
    unapproved = ReorderDecisionPackage(
        sku_profile=profile,
        urgency=UrgencyLevel.RED,
        recommended_qty=500,
        approved=False,  # not approved
    )
    pos = gen.generate_pos([unapproved], SUPPLIER_CONFIGS)
    assert pos == []


def test_empty_packages_returns_empty():
    gen = _make_generator()
    pos = gen.generate_pos([], SUPPLIER_CONFIGS)
    assert pos == []


# ── Tests — Email Body Rendering ──────────────────────────────────────────────


def test_email_body_english_contains_po_number():
    gen = _make_generator()
    packages = [_make_package("SKU-A", "sup-001", 500)]
    pos = gen.generate_pos(packages, SUPPLIER_CONFIGS)
    body = gen.render_po_email_body(pos[0])
    assert pos[0].po_number in body


def test_email_body_english_contains_sku():
    gen = _make_generator()
    packages = [_make_package("SKU-A", "sup-001", 500)]
    pos = gen.generate_pos(packages, SUPPLIER_CONFIGS)
    body = gen.render_po_email_body(pos[0])
    assert "SKU-A" in body


def test_email_body_spanish_when_language_es():
    gen = _make_generator()
    packages = [_make_package("SKU-A", "sup-001", 500)]
    # Use Spanish supplier config
    supplier_configs_es = {
        "sup-001": {
            "email": "pedidos@sup001.com",
            "payment_terms": "Net 30",
            "language": "es",
            "min_order_cases": 100,
        },
    }
    pos = gen.generate_pos(packages, supplier_configs_es)
    body = gen.render_po_email_body(pos[0])
    # Spanish body should contain Spanish words
    assert any(word in body.lower() for word in ["estimado", "orden", "pedido", "cajas", "proveedor"])
