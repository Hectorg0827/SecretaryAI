"""QuickBooks inventory snapshot fixture gate: NO provider/network/database access."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.qb_desktop import QBDesktopAdapter
from app.intelligence.compliance.qb_inventory_snapshot_fixture import (
    preview_synthetic_qb_inventory_snapshot,
)

TENANT = "00000000-0000-4000-8000-000000000011"
FOREIGN = "00000000-0000-4000-8000-000000000099"
BOOK_ID = "00000000-0000-4000-8000-000000000021"
BINDING_ID = "00000000-0000-4000-8000-000000000022"
FIXTURE_AT = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
EVALUATED = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
BOOK = {
    "id": BOOK_ID, "company_id": TENANT, "sku": "SYN-NJ-001",
    "list_price_per_case": "71.250000", "currency": "USD",
    "effective_from": "2026-12-01", "effective_to": None,
    "status": "approved", "customer_ref": "",
    "evidence_ref": "synthetic-approval-document",
}
BINDING = {
    "id": BINDING_ID, "company_id": TENANT, "price_book_id": BOOK_ID,
    "provider": "qbo", "source_item_id": "QBO-ITEM-42",
    "source_revision": "synthetic-v2", "source_sku_hint": "SYN-NJ-001",
    "claimed_currency": "USD", "candidate_unit_basis": "single_unit",
    "candidate_units_per_case": 12,
    "candidate_document_ref": "synthetic-document-unique",
    "identity_state": "candidate_unverified",
}


def qbo_item(**changes):
    raw = {
        "Id": "QBO-ITEM-42", "Name": "Sample Item",
        "Sku": "SYN-NJ-001", "QtyOnHand": "42",
        "UnitPrice": "5.937500", "PurchaseCost": "1.500000",
    }
    raw.update(changes)
    # Only a pure normalizer is invoked, never get_inventory().
    return QBOnlineAdapter._normalize_inventory_item(object.__new__(QBOnlineAdapter), raw)


def qbd_item(**changes):
    raw = {
        "id": "QBD-ITEM-42", "name": "Sample Item",
        "salesDescription": "Not a SKU -- marketing copy",
        "quantityOnHand": "42", "salesPrice": "5.937500",
        "purchaseCost": "1.500000",
    }
    raw.update(changes)
    return QBDesktopAdapter._normalize_inventory_item(object.__new__(QBDesktopAdapter), raw)


def preview(**changes):
    params = {
        "company_id": TENANT, "provider": "qbo",
        "expected_connection_ref": "synthetic-connection-ref",
        "fixture_company_id": TENANT,
        "fixture_connection_ref": "synthetic-connection-ref",
        "fixture_observed_at": FIXTURE_AT, "evaluated_at": EVALUATED,
        "complete_fixture": True,
        "effective_date": date(2026, 12, 1),
        "books": [dict(BOOK)], "bindings": [dict(BINDING)],
        "items": [qbo_item()],
    }
    params.update(changes)
    return preview_synthetic_qb_inventory_snapshot(**params)


def test_exact_provider_item_id_decimal_case_price_match_remains_unverified():
    result = preview()
    line = result["lines"][0]
    assert line["exact_provider_item_id_found_in_fixture"] is True
    assert line["observed_case_amount"] == "71.250000"
    assert line["numeric_comparison"] == "numeric_match_unverified"
    assert "currency_not_verified_in_provider_item_contract" in line["blockers"]
    assert "candidate_case_pack_not_independently_verified" in line["blockers"]
    assert "freight_and_warehouse_costs_not_reconciled" in line["blockers"]
    assert result["live_provider_called"] is False
    assert result["live_connection_authenticated"] is False
    assert result["observation_saved"] is False
    assert result["price_written"] is False
    assert result["independent_accounting_reconciled"] is False
    assert result["ready_to_file"] is False
    assert result["submission_permitted"] is False


def test_one_small_unit_price_change_is_not_rounded_away():
    result = preview(items=[qbo_item(UnitPrice="5.937501")])
    line = result["lines"][0]
    assert line["observed_case_amount"] == "71.250012"
    assert line["numeric_comparison"] == "numeric_difference"
    assert "numeric_case_price_difference" in line["blockers"]


def test_qbd_description_is_not_sku_even_with_exact_provider_item_id():
    binding = dict(BINDING, provider="qbd", source_item_id="QBD-ITEM-42")
    result = preview(provider="qbd", bindings=[binding], items=[qbd_item()])
    line = result["lines"][0]
    assert line["exact_provider_item_id_found_in_fixture"] is True
    assert line["numeric_comparison"] == "numeric_match_unverified"
    assert "qbd_sales_description_cannot_prove_sku" in line["blockers"]
    assert result["independent_accounting_reconciled"] is False


def test_exact_qbo_item_id_is_required_even_if_sku_matches():
    binding = dict(BINDING, source_item_id="QBO-OTHER-ID")
    result = preview(bindings=[binding])
    line = result["lines"][0]
    assert line["numeric_comparison"] == "not_comparable"
    assert line["exact_provider_item_id_found_in_fixture"] is False
    assert "bound_item_not_in_complete_synthetic_fixture" in line["blockers"]


def test_qbo_item_sku_mismatch_prevents_numeric_match_even_with_exact_id():
    result = preview(items=[qbo_item(Sku="DUPLICATE-MISMATCH")])
    line = result["lines"][0]
    assert line["numeric_comparison"] == "not_comparable"
    assert "qbo_sku_hint_or_source_mismatch" in line["blockers"]


def test_qbo_candidate_hint_mismatch_prevents_numeric_match_even_with_exact_id():
    result = preview(bindings=[dict(BINDING, source_sku_hint="WRONG")])
    assert result["lines"][0]["numeric_comparison"] == "not_comparable"


def test_zero_or_missing_unit_price_is_not_evidence_of_zero_list_price():
    result = preview(items=[qbo_item(UnitPrice="0")])
    assert result["lines"][0]["numeric_comparison"] == "not_comparable"
    assert "provider_price_zero_or_unavailable" in result["lines"][0]["blockers"]
    # Existing normalizer defaults missing UnitPrice to 0; gate blocks it.
    result = preview(items=[qbo_item(UnitPrice=None)])
    assert result["lines"][0]["numeric_comparison"] == "not_comparable"


@pytest.mark.parametrize("change,blocker", [
    ({"candidate_unit_basis": "unknown", "candidate_units_per_case": None},
     "provider_unit_basis_unknown"),
    ({"claimed_currency": "EUR"}, "candidate_currency_mismatch_no_fx_inference"),
    ({"source_revision": ""}, "candidate_source_revision_missing"),
    ({"candidate_document_ref": ""}, "candidate_item_mapping_document_missing"),
])
def test_missing_or_conflicting_mapping_evidence_blocks_numeric_equivalence(change, blocker):
    result = preview(bindings=[dict(BINDING, **change)])
    assert result["lines"][0]["numeric_comparison"] == "not_comparable"
    assert blocker in result["lines"][0]["blockers"]


def test_case_basis_compares_per_case_without_inventing_case_pack_count():
    binding = dict(BINDING, candidate_unit_basis="case", candidate_units_per_case=None)
    result = preview(bindings=[binding], items=[qbo_item(UnitPrice="71.250000")])
    assert result["lines"][0]["numeric_comparison"] == "numeric_match_unverified"
    assert "candidate_case_pack_not_independently_verified" not in result["lines"][0]["blockers"]


def test_no_binding_or_multiple_bindings_never_autoselect_item():
    result = preview(bindings=[])
    assert result["lines"][0]["numeric_comparison"] == "not_comparable"
    assert "explicit_provider_item_binding_missing" in result["lines"][0]["blockers"]
    result = preview(bindings=[
        dict(BINDING),
        dict(BINDING, id="another-mapping", source_revision="later"),
    ])
    assert result["lines"][0]["numeric_comparison"] == "not_comparable"
    assert "ambiguous_provider_item_binding" in result["lines"][0]["blockers"]


def test_duplicate_provider_item_ids_fail_closed_for_entire_fixture():
    with pytest.raises(ValueError, match="Provider item ID"):
        preview(items=[qbo_item(), qbo_item(Name="Duplicate")])


@pytest.mark.parametrize("change", [
    {"company_id": FOREIGN},
    {"fixture_company_id": FOREIGN},
    {"fixture_connection_ref": "different-connection"},
    {"expected_connection_ref": ""},
    {"complete_fixture": False},
    {"fixture_observed_at": datetime(2026, 11, 20, tzinfo=timezone.utc)},
    {"fixture_observed_at": datetime(2026, 11, 14, 12)},
    {"effective_date": "2026-12-01"},
    {"provider": "unknown"},
    {"books": [dict(BOOK), dict(BOOK)]},
    {"bindings": [dict(BINDING, company_id=FOREIGN)]},
    {"bindings": [dict(BINDING, provider="qbd")]},
    {"bindings": [dict(BINDING, identity_state="verified")]},
    {"bindings": [dict(BINDING, price_book_id=FOREIGN)]},
    {"bindings": [dict(BINDING, candidate_unit_basis="single_unit",
                       candidate_units_per_case=0)]},
    {"bindings": [dict(BINDING, candidate_unit_basis="case",
                       candidate_units_per_case=12)]},
])
def test_tenant_scope_provenance_unit_basis_and_timestamp_fail_closed(change):
    with pytest.raises(ValueError):
        preview(**change)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1.0"])
def test_invalid_negative_or_nonfinite_provider_sale_price_rejected(amount):
    with pytest.raises(ValueError):
        preview(items=[qbo_item(UnitPrice=amount)])


def test_customer_specific_price_and_unapproved_book_never_match():
    result = preview(books=[
        dict(BOOK, customer_ref="special-customer", status="draft")
    ])
    line = result["lines"][0]
    assert line["numeric_comparison"] == "not_comparable"
    assert "customer_specific_price_scope_not_confirmed" in line["blockers"]
    assert "price_book_approval_pending" in line["blockers"]


def test_offline_fixture_never_invokes_real_adapter_or_database_methods(monkeypatch):
    async def never(*_args, **_kwargs):
        raise AssertionError("Unexpected live QuickBooks request")
    monkeypatch.setattr(QBOnlineAdapter, "get_inventory", never)
    monkeypatch.setattr(QBDesktopAdapter, "get_inventory", never)
    result = preview()
    assert result["live_provider_called"] is False
    assert result["observation_saved"] is False


def test_fixture_gate_is_not_exposed_through_an_api_route():
    from app.api.insights import price_posting_capture
    assert all("fixture" not in r.path for r in price_posting_capture.router.routes)
