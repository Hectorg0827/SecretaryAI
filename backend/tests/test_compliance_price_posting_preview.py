"""Price-posting Phase 1 synthetic tests: documentary review only.

No provider, QuickBooks, Supabase or regulatory agency network calls.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.insights import price_posting as api
from app.intelligence.compliance.price_posting_preview import (
    compile_price_posting_preview,
)

COMPANY_A = "00000000-0000-4000-8000-000000000001"
COMPANY_B = "00000000-0000-4000-8000-000000000002"
BOOK_ID = "00000000-0000-4000-8000-000000000101"
RULE_ID = "00000000-0000-4000-8000-000000000201"
USER = {"company_id": COMPANY_A, "sub": "00000000-0000-4000-8000-000000000300",
        "role": "owner"}

RULE = {
    "id": RULE_ID, "program_key": "NJ_ECPL", "version": 1,
    "country_code": "US", "region_code": "NJ",
    "program_name": "Synthetic NJ CPL",
    "required_details": [
        "brand_name", "product_type", "units_per_case", "upc",
        "brand_registration_number", "license_number", "terms_and_conditions",
    ],
    "allowed_channels": ["retail"],
    "product_types": ["wine", "beer", "malt", "spirits"],
    "authority_url": "https://www.nj.gov/oag/abc/downloads/AN%202025-03-eCPL-Advisory-Notice.pdf",
    "authority_title": "NJ ABC",
    "official_template_url": None,
    "applicability_note": "Candidate only",
    "filing_deadline_rule": None,
}
BOOK = {
    "id": BOOK_ID, "company_id": COMPANY_A, "sku": "SYNTH-001",
    "package_label": "Synthetic case", "market_code": "NJ", "channel": "retail",
    "customer_ref": "", "currency": "USD", "list_price_per_case": "78.123456",
    "effective_from": "2026-11-01", "effective_to": None,
    "revision": 1, "status": "approved", "cost_snapshot_id": "cost-synthetic",
    "evidence_ref": "source-synthetic", "approved_by": "human-synthetic",
    "approved_at": "2026-10-25T12:00:00+00:00",
}
DETAILS = {
    "brand_name": "Synthetic Wine", "product_type": "wine",
    "units_per_case": 12, "upc": "000000000012",
    "brand_registration_number": "SYN-NJ", "license_number": "NJ-SYN",
    "terms_and_conditions": "No promotional discounts",
}


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters = []
        self.id_filter = None
        self.limit_n = None

    def select(self, *_args):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def in_(self, name, values):
        assert name == "id"
        self.id_filter = set(values)
        return self

    def execute(self):
        if self.name == self.db.fail_table:
            raise RuntimeError("Synthetic source outage")
        if self.name == "compliance_price_posting_rule_versions":
            rows = [dict(self.db.rule)]
        elif self.name == "compliance_price_posting_rule_reviews":
            rows = [dict(self.db.review)] if self.db.review else []
        elif self.name == "pricing_price_book":
            rows = [dict(self.db.book)]
        else:
            raise AssertionError(f"Unexpected table {self.name}")
        rows = [
            row for row in rows
            if all(str(row.get(field)) == str(v) for field, v in self.filters)
            and (self.id_filter is None or str(row.get("id")) in self.id_filter)
        ]
        return SimpleNamespace(data=rows[:self.limit_n] if self.limit_n is not None else rows)


class FakeDB:
    def __init__(self):
        self.rule = dict(RULE)
        self.book = dict(BOOK)
        self.review = None
        self.fail_table = None
        self.calls = []

    def table(self, name):
        self.calls.append(name)
        return Query(self, name)

    def rpc(self, *args, **kwargs):
        raise AssertionError("Price-posting preview must never mutate data")


def make_request(**changes):
    params = {
        "program_key": "NJ_ECPL",
        "price_book_ids": [UUID(BOOK_ID)],
        "effective_date": date(2026, 11, 1),
        "line_details": {BOOK_ID: dict(DETAILS)},
    }
    params.update(changes)
    return api.PricePostingPreviewRequest(**params)


@pytest.mark.asyncio
async def test_unverified_rule_blocks_submission_even_with_good_pricebook():
    db = FakeDB()
    result = await api.preview_price_posting(make_request(), user=USER, db=db)
    assert result["human_rule_review_recorded"] is False
    assert result["submission_permitted"] is False
    assert result["agency_template_generated"] is False
    assert result["live_price_changed"] is False
    assert result["quickbooks_write_performed"] is False
    assert result["lines"][0]["list_price_per_case"] == "78.123456"
    assert "human_rule_verification_required" in result["blockers"]
    assert "filing_deadline_unverified" in result["blockers"]
    assert "current_official_template_unverified" in result["blockers"]
    assert "pricing_price_book" in db.calls


@pytest.mark.asyncio
async def test_unapproved_price_book_and_missing_product_data_block():
    db = FakeDB()
    db.book["status"] = "draft"
    result = await api.preview_price_posting(
        make_request(line_details={BOOK_ID: {"brand_name": "Synthetic Wine"}}),
        user=USER, db=db,
    )
    assert "price_book_not_approved" in result["lines"][0]["blockers"]
    assert "missing_or_invalid_units_per_case" in result["lines"][0]["blockers"]
    assert result["submission_permitted"] is False


@pytest.mark.asyncio
async def test_price_book_owned_by_different_tenant_is_never_returned():
    db = FakeDB()
    db.book["company_id"] = COMPANY_B
    with pytest.raises(HTTPException) as error:
        await api.preview_price_posting(make_request(), user=USER, db=db)
    assert error.value.status_code == 404
    assert "not found" in error.value.detail
    assert not hasattr(db, "writes")


@pytest.mark.asyncio
async def test_price_book_outage_fails_closed():
    db = FakeDB()
    db.fail_table = "pricing_price_book"
    with pytest.raises(HTTPException) as error:
        await api.preview_price_posting(make_request(), user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_missing_rulebook_fails_closed_not_no_requirement():
    db = FakeDB()
    db.fail_table = "compliance_price_posting_rule_versions"
    with pytest.raises(HTTPException) as error:
        await api.preview_price_posting(make_request(), user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_review_source_outage_fails_closed():
    db = FakeDB()
    db.fail_table = "compliance_price_posting_rule_reviews"
    with pytest.raises(HTTPException) as error:
        await api.preview_price_posting(make_request(), user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_bogus_human_review_proof_is_not_verified():
    db = FakeDB()
    db.review = {
        "id": "synthetic-review", "company_id": COMPANY_A,
        "rule_id": RULE_ID, "reviewer_id": USER["sub"],
        "reviewed_at": "2026-11-15T10:00:00Z",
        "evidence_ref": "", "review_note": "not enough",
    }
    with pytest.raises(HTTPException) as error:
        await api.preview_price_posting(make_request(), user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_valid_human_review_is_a_precondition_not_filing_authorization():
    db = FakeDB()
    db.review = {
        "id": "synthetic-review", "company_id": COMPANY_A,
        "rule_id": RULE_ID, "reviewer_id": USER["sub"],
        "reviewed_at": "2026-11-15T10:00:00Z",
        "evidence_ref": "independent-authority-evidence",
        "review_note": "I reviewed this exact rule against the official authority document.",
    }
    result = await api.preview_price_posting(make_request(), user=USER, db=db)
    assert result["human_rule_review_recorded"] is True
    assert result["submission_permitted"] is False
    assert "filing_deadline_unverified" in result["blockers"]


@pytest.mark.asyncio
async def test_program_list_exposes_unverified_state_and_urls():
    db = FakeDB()
    response = await api.list_price_posting_programs(user=USER, db=db)
    assert response["agency_submission_enabled"] is False
    assert response["programs"][0]["human_verified_for_tenant"] is False
    assert response["programs"][0]["authority_url"].startswith("https://www.nj.gov/")


def test_preview_requires_live_tenant_price_book_and_no_fake_arithmetic():
    book = dict(BOOK, customer_ref="customer-sensitive",
                list_price_per_case="not-money")
    out = compile_price_posting_preview(
        rule=RULE, verified=False, price_book_rows=[book],
        detail_by_book_id={BOOK_ID: DETAILS},
        requested_effective=date(2026, 11, 1),
    )
    blockers = out["lines"][0]["blockers"]
    assert "customer_specific_pricing_requires_manual_trade_practice_review" in blockers
    assert "source_price_invalid" in blockers
    assert out["submission_permitted"] is False


def test_jurisdiction_mismatch_is_explicit():
    out = compile_price_posting_preview(
        rule=RULE, verified=False, price_book_rows=[dict(BOOK, market_code="NY")],
        detail_by_book_id={BOOK_ID: DETAILS},
        requested_effective=date(2026, 11, 1),
    )
    assert "market_mismatch" in out["lines"][0]["blockers"]


def test_rule_requires_real_county_detail_for_florida():
    rule = dict(RULE, program_key="FL_MALT_NOTICE", region_code="FL",
                required_details=["product_type", "counties", "units_per_case"],
                product_types=["beer", "malt"])
    book = dict(BOOK, market_code="FL")
    out = compile_price_posting_preview(
        rule=rule, verified=False, price_book_rows=[book],
        detail_by_book_id={BOOK_ID: {"product_type": "beer", "counties": [],
                                     "units_per_case": 24}},
        requested_effective=date(2026, 11, 1),
    )
    assert "missing_or_invalid_counties" in out["lines"][0]["blockers"]


def test_insights_router_mounts_price_posting_automatically():
    from app.api import insights
    assert "price_posting" in insights.MOUNTED
    paths = [r.path for r in insights.router.routes]
    assert "/price-posting/preview" in paths
    assert "/price-posting/programs" in paths
