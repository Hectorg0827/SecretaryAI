"""Synthetic accounting-observation arithmetic and read-only API validation.

These tests never fetch QBO/QBD data or submit regulator documents.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

from app.api.insights import price_posting_accounting as api
from app.intelligence.compliance.accounting_observation_comparison import (
    compare_accounting_observations,
)

TENANT = "00000000-0000-4000-8000-000000000011"
FOREIGN = "00000000-0000-4000-8000-000000000099"
BOOK = "00000000-0000-4000-8000-000000000021"
OBS_ID = "00000000-0000-4000-8000-000000000022"
USER = {"company_id": TENANT, "sub": "00000000-0000-4000-8000-000000000012",
        "role": "pricing_billbacks"}
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
BOOK_ROW = {
    "id": BOOK, "company_id": TENANT, "sku": "TEST-NJ-A",
    "package_label": "12x750mL", "market_code": "NJ",
    "channel": "retail", "customer_ref": "", "currency": "USD",
    "list_price_per_case": "71.250000", "effective_from": "2026-12-01",
    "effective_to": None, "revision": 3, "status": "approved",
    "cost_snapshot_id": None, "evidence_ref": "synthetic",
    "approved_by": "someone-else", "approved_at": "2026-11-14T11:00:00Z",
    "created_by": "someone",
}
OBS_ROW = {
    "id": OBS_ID, "company_id": TENANT, "price_book_id": BOOK,
    "provider": "qbo", "source_item_id": "synthetic-qbo-item-41",
    "source_revision": "version-7", "sku": "TEST-NJ-A",
    "currency": "USD", "observed_unit_price": "5.937500",
    "unit_basis": "single_unit", "units_per_case": 12,
    "source_document_ref": "synthetic-documented-connector-state",
    "observed_at": "2026-11-14T12:00:00Z",
    "source_origin": "synthetic_test",
    "observation_state": "internally_recorded_unverified",
}


def compare(*, books=None, observations=None):
    return compare_accounting_observations(
        company_id=TENANT,
        books=books if books is not None else [dict(BOOK_ROW)],
        observations=observations if observations is not None else [dict(OBS_ROW)],
        evaluated_at=NOW,
    )


def test_exact_decimal_case_conversion_matches_but_never_certifies():
    result = compare()
    assert result["numeric_matches_unverified"] == 1
    line = result["checks"][0]
    assert line["observed_amount_per_case"] == "71.250000"
    assert line["numeric_comparison"] == "numeric_match_unverified"
    assert "synthetic_test_source_only" in line["blockers"]
    assert "case_pack_conversion_not_independently_verified" in line["blockers"]
    assert result["independent_qbo_qbd_reconciliation_confirmed"] is False
    assert result["ready_to_file"] is False
    assert result["submission_permitted"] is False
    assert result["qbo_qbd_live_data_accessed"] is False


def test_pre_recorded_case_amount_comparison_exact_no_conversion():
    row = dict(OBS_ROW, provider="qbd", unit_basis="case",
               units_per_case=None, observed_unit_price="71.250000")
    result = compare(observations=[row])
    assert result["numeric_matches_unverified"] == 1
    assert result["checks"][0]["provider"] == "qbd"


def test_one_cent_difference_is_visible_not_rounded_to_match():
    row = dict(OBS_ROW, observed_unit_price="5.938334")
    result = compare(observations=[row])
    assert result["checks"][0]["numeric_comparison"] == "numeric_difference"
    assert result["checks"][0]["observed_amount_per_case"] == "71.260008"
    assert "accounting_amount_difference" in result["checks"][0]["blockers"]


def test_missing_observation_never_invents_zero_price_or_success():
    result = compare(observations=[])
    assert result["uncomparable"] == 1
    assert result["checks"][0]["observed_amount_per_case"] is None
    assert "accounting_observation_missing" in result["checks"][0]["blockers"]


def test_multiple_stored_observations_never_choose_newest_automatically():
    additional = dict(OBS_ROW, id="another-synthetic-id")
    result = compare(observations=[dict(OBS_ROW), additional])
    assert result["uncomparable"] == 1
    assert "ambiguous_accounting_observations_require_selection" in result["checks"][0]["blockers"]


@pytest.mark.parametrize("overrides,code", [
    ({"sku": "different"}, "accounting_sku_mismatch"),
    ({"currency": "EUR"}, "currency_mismatch_no_fx_inference"),
    ({"provider": "unknown"}, "accounting_provider_unknown"),
    ({"source_item_id": ""}, "accounting_item_identity_missing"),
    ({"source_revision": ""}, "accounting_revision_missing"),
    ({"source_document_ref": ""}, "accounting_document_reference_missing"),
    ({"observed_at": "2026-11-17T00:00:00Z"}, "accounting_observation_from_future"),
])
def test_mismatched_identifiers_not_numerically_comparable(overrides, code):
    result = compare(observations=[dict(OBS_ROW, **overrides)])
    line = result["checks"][0]
    assert line["numeric_comparison"] == "not_comparable"
    assert code in line["blockers"]
    assert result["independent_qbo_qbd_reconciliation_confirmed"] is False


def test_customer_specific_price_cannot_match_global_item_rate():
    book = dict(BOOK_ROW, customer_ref="customer-A")
    result = compare(books=[book])
    assert result["checks"][0]["numeric_comparison"] == "not_comparable"
    assert "customer_specific_scope_not_equivalent_to_global_item_price" in result["checks"][0]["blockers"]


def test_unknown_unit_basis_cannot_be_assumed_case_price():
    result = compare(observations=[dict(
        OBS_ROW, unit_basis="unknown", units_per_case=None,
    )])
    assert result["uncomparable"] == 1
    assert "accounting_unit_basis_unknown" in result["checks"][0]["blockers"]


@pytest.mark.parametrize("row", [
    dict(OBS_ROW, company_id=FOREIGN),
    dict(OBS_ROW, observation_state="verified_by_agent"),
    dict(OBS_ROW, observed_unit_price="NaN"),
    dict(OBS_ROW, observed_unit_price="-2.0"),
    dict(OBS_ROW, units_per_case=0),
    dict(OBS_ROW, observed_at="2026-11-14"),
    dict(OBS_ROW, price_book_id="orphan"),
])
def test_invalid_or_cross_tenant_source_fails_closed(row):
    with pytest.raises(ValueError):
        compare(observations=[row])


def test_duplicate_observation_ids_are_not_accepted():
    with pytest.raises(ValueError):
        compare(observations=[dict(OBS_ROW), dict(OBS_ROW)])


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters = []
        self.max_rows = 1000

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, str(value)))
        return self

    def in_(self, key, values):
        self.filters.append((key, {str(x) for x in values}))
        return self

    def limit(self, n):
        self.max_rows = n
        return self

    def execute(self):
        if self.db.fail_source == self.name:
            raise RuntimeError("Synthetic source outage")
        rows = self.db.sources.get(self.name, [])
        found = [
            dict(row) for row in rows
            if all(
                str(row.get(key)) == expected if isinstance(expected, str)
                else str(row.get(key)) in expected
                for key, expected in self.filters
            )
        ]
        return SimpleNamespace(data=found[:self.max_rows])


class FakeDB:
    def __init__(self):
        self.sources = {
            "pricing_price_book": [dict(BOOK_ROW)],
            "pricing_accounting_price_observations": [dict(OBS_ROW)],
        }
        self.fail_source = None
        self.calls = []

    def table(self, name):
        self.calls.append(name)
        assert name in self.sources
        return Query(self, name)


async def endpoint(db, ids=None):
    request = api.AccountingObservationComparisonRequest(
        price_book_ids=ids or [UUID(BOOK)]
    )
    return await api.compare_stored_accounting_observations(
        request, Response(), user=USER, db=db,
    )


@pytest.mark.asyncio
async def test_read_only_api_uses_only_tenant_stored_evidence():
    db = FakeDB()
    result = await endpoint(db)
    assert result["status"] == "internal_numeric_comparison_only"
    assert result["ready_to_file"] is False
    assert result["qbo_qbd_live_data_accessed"] is False
    assert db.calls == [
        "pricing_price_book", "pricing_accounting_price_observations"
    ]


@pytest.mark.asyncio
async def test_unavailable_observation_table_is_not_treated_as_zero():
    db = FakeDB()
    db.fail_source = "pricing_accounting_price_observations"
    with pytest.raises(HTTPException) as exc:
        await endpoint(db)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_foreign_tenant_price_book_not_found_without_id_leak():
    db = FakeDB()
    db.sources["pricing_price_book"][0]["company_id"] = FOREIGN
    with pytest.raises(HTTPException) as exc:
        await endpoint(db)
    assert exc.value.status_code == 404
    assert "pricing_accounting_price_observations" not in db.calls


@pytest.mark.asyncio
async def test_cross_tenant_accounting_row_is_invalid_not_ignored():
    db = FakeDB()
    db.sources["pricing_accounting_price_observations"][0]["company_id"] = FOREIGN
    result = await endpoint(db)
    assert "accounting_observation_missing" in result["checks"][0]["blockers"]
    assert result["ready_to_file"] is False


@pytest.mark.asyncio
async def test_duplicate_price_book_ids_rejected_before_database():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, ids=[UUID(BOOK), UUID(BOOK)])
    assert exc.value.status_code == 422
    assert not db.calls


def test_insight_route_is_discovered_without_manually_wiring_shared_router():
    from app.api import insights
    assert "price_posting_accounting" in insights.MOUNTED
    assert any(route.path == "/compare" for route in api.router.routes)
