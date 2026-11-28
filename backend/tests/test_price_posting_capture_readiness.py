"""Synthetic QB Online/Desktop item-ID capture planning; no live connector IO."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

from app.api.insights import price_posting_capture as api
from app.intelligence.compliance.accounting_capture_readiness import (
    compile_capture_mapping_readiness,
)

TENANT = "00000000-0000-4000-8000-000000000011"
FOREIGN = "00000000-0000-4000-8000-000000000099"
BOOK_ID = "00000000-0000-4000-8000-000000000021"
BINDING_ID = "00000000-0000-4000-8000-000000000022"
USER = {"company_id": TENANT, "sub": "00000000-0000-4000-8000-000000000012", "role": "owner"}
BOOK_ROW = {
    "id": BOOK_ID, "company_id": TENANT, "sku": "SYN-NJ-001",
    "package_label": "12x750mL", "market_code": "NJ", "channel": "retail",
    "customer_ref": "", "currency": "USD",
    "list_price_per_case": "71.25", "effective_from": "2026-12-01",
    "effective_to": None, "revision": 3, "status": "approved",
    "evidence_ref": "synthetic-document-evidence",
}
BINDING_ROW = {
    "id": BINDING_ID, "company_id": TENANT, "price_book_id": BOOK_ID,
    "provider": "qbo", "source_item_id": "synthetic-QBO-id-001",
    "source_revision": "version-1", "source_sku_hint": "SYN-NJ-001",
    "claimed_currency": "USD", "candidate_unit_basis": "single_unit",
    "candidate_units_per_case": 12,
    "candidate_document_ref": "synthetic-id-map-document",
    "identity_state": "candidate_unverified",
}


def plan(*, provider="qbo", books=None, bindings=None, when=date(2026, 12, 1)):
    return compile_capture_mapping_readiness(
        company_id=TENANT, provider=provider,
        books=books if books is not None else [dict(BOOK_ROW)],
        bindings=bindings if bindings is not None else [dict(BINDING_ROW)],
        effective_date=when,
    )


def test_explicit_qbo_item_mapping_remains_unverified_and_no_provider_read():
    result = plan()
    item = result["items"][0]
    assert item["candidate_provider_item_id_present"] is True
    assert item["candidate_units_per_case"] == 12
    assert item["state"] == "needs_human_source_mapping"
    assert "per_unit_to_case_conversion_requires_independent_verification" in item["blockers"]
    assert result["source_items_observed_live"] is False
    assert result["provider_api_called"] is False
    assert result["observation_saved"] is False
    assert result["safe_to_capture_live"] is False
    assert result["submission_permitted"] is False


def test_missing_mapping_does_not_invent_provider_id():
    result = plan(bindings=[])
    assert result["not_mapped"] == 1
    assert "provider_item_id_binding_missing" in result["items"][0]["blockers"]
    assert result["item_identity_certified"] is False


def test_duplicate_mapping_never_autoselects_newest():
    other = dict(BINDING_ROW, id="different-id", source_revision="version-2")
    result = plan(bindings=[dict(BINDING_ROW), other])
    assert result["ambiguous"] == 1
    assert result["items"][0]["candidate_provider_item_id_present"] is False
    assert "multiple_provider_item_bindings_require_human_selection" in result["items"][0]["blockers"]


def test_qbd_sales_description_hint_is_never_authoritative_sku():
    qbd = dict(
        BINDING_ROW, provider="qbd",
        source_sku_hint="salesDescription may be marketing copy",
        source_item_id="conductor-item-id-not-a-description",
    )
    result = plan(provider="qbd", bindings=[qbd])
    item = result["items"][0]
    assert "qbd_sales_description_is_not_an_authoritative_sku" in item["blockers"]
    assert item["candidate_provider_item_id_present"] is True
    assert result["item_identity_certified"] is False


def test_qbo_sku_hint_is_not_sufficient_and_mismatch_is_a_blocker():
    result = plan(bindings=[dict(BINDING_ROW, source_sku_hint="DIFFERENT")])
    assert "qbo_sku_hint_mismatch" in result["items"][0]["blockers"]
    assert result["item_identity_certified"] is False


@pytest.mark.parametrize("change,reason", [
    ({"claimed_currency": "EUR"}, "currency_mismatch_no_fx_inference"),
    ({"candidate_unit_basis": "unknown", "candidate_units_per_case": None},
     "source_unit_basis_not_established"),
    ({"source_item_id": ""}, "provider_item_id_missing"),
    ({"source_revision": ""}, "provider_item_revision_missing"),
    ({"candidate_document_ref": ""}, "item_binding_document_missing"),
])
def test_missing_identity_currency_or_units_are_not_approved(change, reason):
    result = plan(bindings=[dict(BINDING_ROW, **change)])
    assert reason in result["items"][0]["blockers"]
    assert result["safe_to_capture_live"] is False


def test_customer_specific_and_not_yet_effective_are_blocked():
    book = dict(BOOK_ROW, customer_ref="private-buyer", status="draft")
    result = plan(books=[book], when=date(2026, 11, 15))
    blockers = result["items"][0]["blockers"]
    assert "customer_specific_price_scope_not_supported" in blockers
    assert "price_book_not_effective_on_requested_date" in blockers
    assert "price_book_approval_pending" in blockers


@pytest.mark.parametrize("binding", [
    dict(BINDING_ROW, company_id=FOREIGN),
    dict(BINDING_ROW, provider="qbd"),
    dict(BINDING_ROW, identity_state="verified"),
    dict(BINDING_ROW, price_book_id=FOREIGN),
    dict(BINDING_ROW, candidate_units_per_case=0),
    dict(BINDING_ROW, candidate_unit_basis="case", candidate_units_per_case=12),
])
def test_invalid_scope_and_provider_metadata_fail_closed(binding):
    with pytest.raises(ValueError):
        plan(bindings=[binding])


def test_existing_adapters_reveal_desktop_description_is_not_a_true_sku():
    from app.connectors.qb_desktop import QBDesktopAdapter
    from app.connectors.qb_online import QBOnlineAdapter
    qbd = QBDesktopAdapter._normalize_inventory_item(object.__new__(QBDesktopAdapter), {
        "id": "QBD-12", "name": "Product A",
        "salesDescription": "PACK DESCRIPTION, NOT RELIABLE SKU",
        "quantityOnHand": 42, "salesPrice": "5.9375",
    })
    qbo = QBOnlineAdapter._normalize_inventory_item(object.__new__(QBOnlineAdapter), {
        "Id": "QBO-12", "Name": "Product A", "Sku": "SYN-NJ-001",
        "QtyOnHand": 42, "UnitPrice": "5.9375",
    })
    assert qbd.qb_id == "QBD-12"
    assert qbd.sku == "PACK DESCRIPTION, NOT RELIABLE SKU"
    assert qbo.qb_id == "QBO-12"
    assert qbo.sku == "SYN-NJ-001"
    # No adapter .get_inventory() or HTTP I/O is called.


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.max_rows = 1000

    def select(self, *_args): return self
    def eq(self, key, value):
        self.filters.append((key, str(value)))
        return self
    def in_(self, key, values):
        self.filters.append((key, {str(v) for v in values}))
        return self
    def limit(self, value):
        self.max_rows = value
        return self
    def execute(self):
        if self.db.fail_source == self.table:
            raise RuntimeError("Synthetic unavailable data source")
        rows = self.db.sources.get(self.table, [])
        selected = []
        for row in rows:
            if all(
                str(row.get(key)) == value if isinstance(value, str)
                else str(row.get(key)) in value
                for key, value in self.filters
            ):
                selected.append(dict(row))
        return SimpleNamespace(data=selected[:self.max_rows])


class FakeDB:
    def __init__(self):
        self.calls = []
        self.fail_source = None
        self.sources = {
            "pricing_price_book": [dict(BOOK_ROW)],
            "pricing_accounting_capture_bindings": [dict(BINDING_ROW)],
        }

    def table(self, name):
        self.calls.append(name)
        assert name in self.sources
        return Query(self, name)


async def endpoint(db, *, role="owner", ids=None):
    return await api.capture_prerequisites(
        api.CaptureReadinessRequest(
            provider="qbo", effective_date=date(2026, 12, 1),
            price_book_ids=ids if ids is not None else [UUID(BOOK_ID)],
        ),
        Response(), user=dict(USER, role=role), db=db,
    )


@pytest.mark.asyncio
async def test_owner_capture_readiness_only_reads_mapped_internal_tables():
    db = FakeDB()
    result = await endpoint(db)
    assert result["status"] == "capture_prerequisites_only"
    assert result["provider_api_called"] is False
    assert db.calls == ["pricing_price_book", "pricing_accounting_capture_bindings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["pricing_billbacks", "viewer", "sales_rep"])
async def test_nonowner_cannot_read_qb_item_mapping(role):
    db = FakeDB()
    with pytest.raises(HTTPException) as err:
        await endpoint(db, role=role)
    assert err.value.status_code == 403
    assert db.calls == []


@pytest.mark.asyncio
async def test_missing_price_book_source_does_not_query_bindings():
    db = FakeDB()
    db.sources["pricing_price_book"][0]["company_id"] = FOREIGN
    with pytest.raises(HTTPException) as err:
        await endpoint(db)
    assert err.value.status_code == 404
    assert db.calls == ["pricing_price_book"]


@pytest.mark.asyncio
async def test_mapping_query_unavailable_is_error_not_empty():
    db = FakeDB()
    db.fail_source = "pricing_accounting_capture_bindings"
    with pytest.raises(HTTPException) as err:
        await endpoint(db)
    assert err.value.status_code == 503


@pytest.mark.asyncio
async def test_cross_tenant_row_returned_by_service_is_rejected():
    db = FakeDB()
    db.sources["pricing_accounting_capture_bindings"] = [dict(BINDING_ROW, company_id=FOREIGN)]
    result = await endpoint(db)
    # Database tenant filter excludes foreign evidence: absence is not approval.
    assert result["not_mapped"] == 1
    assert result["safe_to_capture_live"] is False


@pytest.mark.asyncio
async def test_duplicate_price_ids_rejected_before_any_reads():
    db = FakeDB()
    with pytest.raises(HTTPException) as err:
        await endpoint(db, ids=[UUID(BOOK_ID), UUID(BOOK_ID)])
    assert err.value.status_code == 422
    assert db.calls == []


def test_insight_router_discovers_read_only_capture_prerequisite():
    from app.api import insights
    assert "price_posting_capture" in insights.MOUNTED
    assert any(route.path == "/readiness" for route in api.router.routes)
