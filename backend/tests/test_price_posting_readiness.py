"""Synthetic price-posting source lineage tests: no QBO, filing or writes."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

from app.api.insights import price_posting as api

TENANT = "00000000-0000-4000-8000-000000000011"
FOREIGN = "00000000-0000-4000-8000-000000000099"
BOOK = "00000000-0000-4000-8000-000000000021"
SNAPSHOT = "00000000-0000-4000-8000-000000000022"
RULE = "00000000-0000-4000-8000-000000000023"
USER = {"company_id": TENANT, "sub": "00000000-0000-4000-8000-000000000024",
        "role": "pricing_billbacks"}

RULE_ROW = {
    "id": RULE, "program_key": "NJ_ECPL", "version": 1,
    "country_code": "US", "region_code": "NJ",
    "program_name": "Synthetic current list",
    "required_details": ["brand_name","product_type","units_per_case","upc"],
    "allowed_channels": ["retail"], "product_types": ["wine","spirits"],
    "authority_url": "https://www.nj.gov/example",
    "authority_title": "NJ source", "official_template_url": None,
    "applicability_note": "Candidate only", "filing_deadline_rule": None,
}
BOOK_ROW = {
    "id": BOOK, "company_id": TENANT, "sku": "SYN-NJ-001",
    "package_label": "12x750mL", "market_code": "NJ", "channel": "retail",
    "customer_ref": "", "currency": "USD", "list_price_per_case": "75.250000",
    "effective_from": "2026-11-01", "effective_to": None,
    "revision": 3, "status": "approved", "cost_snapshot_id": SNAPSHOT,
    "evidence_ref": "synthetic-approval-evidence", "created_by": "synthetic-user-a",
    "approved_by": "synthetic-user-b", "approved_at": "2026-10-31T12:00:00Z",
}
SNAPSHOT_ROW = {
    "id": SNAPSHOT, "company_id": TENANT, "sku": "SYN-NJ-001",
    "currency": "USD", "cost_as_of": "2026-10-25",
    "evidence_status": "reconciled", "source_revision": "source-3",
}
COST_LINE = {
    "company_id": TENANT, "cost_snapshot_id": SNAPSHOT,
    "expense_line_ref": "synthetic-warehouse-line-1",
    "component": "warehouse_storage", "source_document_ref": "synthetic-document-a",
    "source_revision": "source-3", "observation_status": "verified",
}
PRODUCT_ROW = {
    "id": "product-a", "company_id": TENANT,
    "sku": "SYN-NJ-001", "product_type": "wine",
}
LICENSE_ROW = {
    "id": "license-a", "company_id": TENANT, "state_code": "NJ",
    "license_number": "synthetic-license-nj", "product_types": ["wine"],
    "status": "active",
}


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.conditions = []
        self.sets = []
        self.max_rows = 1000

    def select(self, *_):
        return self

    def eq(self, key, value):
        self.conditions.append((key, value))
        return self

    def in_(self, key, values):
        self.sets.append((key, {str(x) for x in values}))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.max_rows = n
        return self

    def execute(self):
        if self.name == self.db.outage:
            raise RuntimeError("synthetic source unavailable")
        rows = [dict(x) for x in self.db.tables.get(self.name, [])]
        if self.name != self.db.malicious_scope:
            rows = [
                x for x in rows
                if all(str(x.get(k)) == str(v) for k, v in self.conditions)
                and all(str(x.get(k)) in vals for k, vals in self.sets)
            ]
        return SimpleNamespace(data=rows[:self.max_rows])


class FakeDB:
    def __init__(self):
        self.tables = {
            "compliance_price_posting_rule_versions": [dict(RULE_ROW)],
            "compliance_price_posting_rule_reviews": [],
            "pricing_price_book": [dict(BOOK_ROW)],
            "pricing_cost_snapshots": [dict(SNAPSHOT_ROW)],
            "pricing_cost_lines": [dict(COST_LINE)],
            "compliance_products": [dict(PRODUCT_ROW)],
            "compliance_licenses": [dict(LICENSE_ROW)],
        }
        self.calls = []
        self.outage = None
        self.malicious_scope = None

    def table(self, name):
        self.calls.append(name)
        if name not in self.tables:
            raise AssertionError("Unexpected private source " + name)
        return Query(self, name)

    def rpc(self, *_args, **_kwargs):
        raise AssertionError("Readiness must never write or call a database RPC")


def request(ids=None, **changes):
    payload = {
        "program_key": "NJ_ECPL",
        "price_book_ids": [UUID(x) for x in (ids or [BOOK])],
        "effective_date": date(2026, 11, 10),
    }
    payload.update(changes)
    return api.PricePostingReadinessRequest(**payload)


async def invoke(db, request_value=None):
    response = Response()
    result = await api.preview_price_posting_source_readiness(
        request_value or request(), response=response, user=USER, db=db,
    )
    assert response.headers["Cache-Control"] == "private, no-store"
    return result


@pytest.mark.asyncio
async def test_scoped_evidence_present_but_never_filing_ready():
    db = FakeDB()
    result = await invoke(db)
    assert result["source_evidence"][0]["sku"] == "SYN-NJ-001"
    assert result["source_evidence"][0]["source_blockers"] == []
    assert result["source_evidence"][0]["cost_basis_recorded_as_reconciled"] is True
    assert result["source_evidence"][0]["matching_license_record_count"] == 1
    assert result["lines"][0]["list_price_per_case"] == "75.250000"
    assert "missing_or_invalid_upc" in result["lines"][0]["blockers"]
    assert "independent_qbo_qbd_reconciliation_not_verified" in result["blockers"]
    assert result["ready_to_file"] is False
    assert result["submission_permitted"] is False
    assert result["agency_template_generated"] is False
    assert result["qbo_qbd_live_data_accessed"] is False
    assert "audit_events" not in db.calls


@pytest.mark.asyncio
async def test_foreign_company_price_id_is_404_without_exposing_row():
    db = FakeDB()
    db.tables["pricing_price_book"][0]["company_id"] = FOREIGN
    with pytest.raises(HTTPException) as error:
        await invoke(db)
    assert error.value.status_code == 404
    assert "pricing_cost_snapshots" not in db.calls


@pytest.mark.asyncio
async def test_invalid_scope_returned_by_service_client_is_503():
    db = FakeDB()
    db.tables["pricing_cost_snapshots"][0]["company_id"] = FOREIGN
    db.malicious_scope = "pricing_cost_snapshots"
    with pytest.raises(HTTPException) as error:
        await invoke(db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("table", [
    "compliance_price_posting_rule_versions",
    "compliance_price_posting_rule_reviews",
    "pricing_price_book", "pricing_cost_snapshots",
    "pricing_cost_lines", "compliance_products", "compliance_licenses",
])
async def test_each_source_outage_fails_closed(table):
    db = FakeDB()
    db.outage = table
    with pytest.raises(HTTPException) as error:
        await invoke(db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_provisional_costs_and_unverified_documents_are_blocked():
    db = FakeDB()
    db.tables["pricing_cost_snapshots"][0]["evidence_status"] = "provisional"
    db.tables["pricing_cost_lines"][0]["observation_status"] = "provisional"
    result = await invoke(db)
    blocks = result["source_evidence"][0]["source_blockers"]
    assert "cost_snapshot_not_reconciled" in blocks
    assert "cost_document_lines_not_verified" in blocks
    assert result["source_evidence"][0]["cost_basis_recorded_as_reconciled"] is False
    assert result["ready_to_file"] is False


@pytest.mark.asyncio
async def test_missing_cost_snapshot_is_a_blocker_not_verified_zero():
    db = FakeDB()
    db.tables["pricing_cost_snapshots"] = []
    db.tables["pricing_cost_lines"] = []
    result = await invoke(db)
    assert "cost_snapshot_missing" in result["source_evidence"][0]["source_blockers"]
    assert result["ready_to_file"] is False


@pytest.mark.asyncio
async def test_orphaned_cost_line_is_invalid_source_not_confirmed_absence():
    db = FakeDB()
    db.tables["pricing_cost_snapshots"] = []
    # A retained line pointing to a missing cost snapshot is not a safe blank.
    with pytest.raises(HTTPException) as error:
        await invoke(db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_unmatched_product_and_license_cannot_be_auto_inferred():
    db = FakeDB()
    db.tables["compliance_products"] = []
    result = await invoke(db)
    codes = result["source_evidence"][0]["source_blockers"]
    assert "compliance_product_record_missing" in codes
    assert "matching_internal_license_record_missing" in codes


@pytest.mark.asyncio
async def test_duplicate_product_sku_is_ambiguous():
    db = FakeDB()
    db.tables["compliance_products"].append(dict(PRODUCT_ROW, id="product-2"))
    result = await invoke(db)
    assert "compliance_product_sku_ambiguous" in result["source_evidence"][0]["source_blockers"]


@pytest.mark.asyncio
async def test_matching_snapshot_must_reconcile_currency_and_sku():
    db = FakeDB()
    db.tables["pricing_cost_snapshots"][0]["sku"] = "different-sku"
    db.tables["pricing_cost_snapshots"][0]["currency"] = "EUR"
    result = await invoke(db)
    codes = result["source_evidence"][0]["source_blockers"]
    assert "cost_snapshot_sku_mismatch" in codes
    assert "cost_snapshot_currency_mismatch" in codes
    assert result["source_evidence"][0]["cost_basis_recorded_as_reconciled"] is False


@pytest.mark.asyncio
async def test_creator_cannot_approve_own_price():
    db = FakeDB()
    db.tables["pricing_price_book"][0]["approved_by"] = "synthetic-user-a"
    result = await invoke(db)
    assert "independent_price_approval_not_confirmed" in result["source_evidence"][0]["source_blockers"]


@pytest.mark.asyncio
async def test_duplicate_price_book_identifiers_rejected_before_source():
    db = FakeDB()
    with pytest.raises(HTTPException) as error:
        await invoke(db, request([BOOK, BOOK]))
    assert error.value.status_code == 422
    assert not db.calls


@pytest.mark.asyncio
async def test_unreturned_price_book_id_cannot_be_treated_as_zero():
    db = FakeDB()
    db.tables["pricing_price_book"] = []
    with pytest.raises(HTTPException) as error:
        await invoke(db)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_product_source_cannot_look_like_verified_classification():
    db = FakeDB()
    db.tables["compliance_products"] = []
    result = await invoke(db)
    assert result["source_evidence"][0]["product_type_from_internal_registry"] is None
    assert result["lines"][0]["draft_product_details"]["product_type"] is None
    assert result["ready_to_file"] is False
