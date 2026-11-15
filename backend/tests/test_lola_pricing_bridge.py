"""Lola pricing bridge: exact-source, tenant and authorization contract tests.

Synthetic rows only. All business sources are mocked; no live provider or
Supabase project accessed and no writes permitted.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.intelligence.lola.pricing_bridge import (
    PricingEvidenceUnavailable, PricingSnapshotNotFound,
    list_cost_snapshot_candidates, review_cost_snapshot,
)

SNAP = "123e4567-e89b-12d3-a456-426614174001"
COMPANY = "tenant-A"
SNAPSHOT = {
    "id": SNAP, "company_id": COMPANY, "sku": "CAB-750ML",
    "shipment_ref": "SHIP-01", "currency": "USD", "accepted_cases": "1200.000",
    "cost_as_of": "2026-11-13", "source_revision": "snapshot-v1",
    "evidence_status": "reconciled",
}
COSTS = {
    "product": "20.000000", "international_freight": "3.200000",
    "insurance": "0.250000", "duty": "1.000000",
    "customs_brokerage": "0.200000", "port_fees": "0.100000",
    "domestic_freight": "0.700000", "excise": "1.000000",
    "other_taxes": "0.000000",
    "warehouse_receiving": "0.100000", "warehouse_storage": "0.100000",
    "warehouse_handling": "0.100000", "warehouse_outbound": "0.200000",
    "warehouse_shrink": "0.100000", "inventory_carrying": "0.100000",
}


def cost_rows():
    return [
        {
            "company_id": COMPANY, "cost_snapshot_id": SNAP,
            "expense_line_ref": f"invoice-line-{i}",
            "component": component, "cost_per_case": amount,
            "source_document_ref": f"document-{i}",
            "source_revision": f"document-v{i}", "allocation_basis": "cases",
            "observation_status": "verified",
        }
        for i, (component, amount) in enumerate(COSTS.items())
    ]


def book_rows():
    return [{
        "id": "pricebook-1", "company_id": COMPANY, "sku": "CAB-750ML",
        "cost_snapshot_id": SNAP, "currency": "USD",
        "status": "approved", "revision": 1, "market_code": "NY",
        "channel": "distributor", "effective_from": "2026-11-13",
        "effective_to": None, "list_price_per_case": "68.500000",
        "evidence_ref": "pricebook-revision", "approved_at": "2026-11-13",
    }]


class Query:
    def __init__(self, records, log, table, *, fail=False):
        self.records, self.log, self.table, self.fail = records, log, table, fail
        self.filters = []
        self.cap = None
    def select(self, columns):
        self.log.append((self.table, "select", columns))
        return self
    def eq(self, field, value):
        self.filters.append((field, value))
        self.log.append((self.table, "eq", field, value))
        return self
    def order(self, column, desc=False):
        self.log.append((self.table, "order", column, desc))
        return self
    def limit(self, count):
        self.cap = count
        return self
    def execute(self):
        if self.fail:
            raise ConnectionError("SECRET provider exception: do not expose")
        rows = [
            item for item in self.records
            if all(str(item.get(field)) == str(value) for field, value in self.filters)
        ]
        return SimpleNamespace(data=deepcopy(rows[:self.cap]))


class Database:
    def __init__(self, snapshots=None, costs=None, books=None, *, fail=None):
        self.records = {
            "pricing_cost_snapshots": [SNAPSHOT] if snapshots is None else snapshots,
            "pricing_cost_lines": cost_rows() if costs is None else costs,
            "pricing_price_book": book_rows() if books is None else books,
        }
        self.log = []
        self.fail = fail
    def table(self, name):
        assert name in self.records, "Only pricing sources may be queried"
        return Query(self.records[name], self.log, name, fail=self.fail == name)
    def assert_read_only_and_scoped(self):
        assert all(entry[1] in ("select", "eq", "order") for entry in self.log)
        for name in self.records:
            if any(entry[0] == name for entry in self.log):
                assert (name, "eq", "company_id", COMPANY) in self.log


def review(db):
    return review_cost_snapshot(company_id=COMPANY, snapshot_id=UUID(SNAP), db=db)


def test_complete_reconciled_snapshot_is_still_not_accounting_certified():
    db = Database()
    result = review(db)
    assert result["source_documentation_complete"] is True
    assert result["recorded_cost_per_case"] == "27.050000"
    assert result["accepted_cases"] == "1200.000"
    assert result["review_state"] == "ready_for_independent_reconciliation"
    assert result["gross_margin_calculated"] is False
    assert result["price_publication_verified"] is False
    assert result["accounting_reconciled"] is False
    assert result["net_revenue_verified"] is False
    assert result["actions_staged"] is False
    assert result["external_writes_executed"] is False
    assert result["price_book_entries"][0]["publication_verified"] is False
    db.assert_read_only_and_scoped()


def test_missing_lines_never_become_zero_landed_cost():
    result = review(Database(costs=[], books=[]))
    assert result["recorded_cost_per_case"] is None
    assert "no_cost_lines" in result["issues_requiring_review"]
    assert "no_linked_price_book" in result["issues_requiring_review"]


def test_missing_warehouse_costs_are_named():
    costs = [r for r in cost_rows() if not r["component"].startswith("warehouse_") and r["component"] != "inventory_carrying"]
    result = review(Database(costs=costs))
    assert result["source_documentation_complete"] is False
    assert "missing_warehouse_cost_categories" in result["issues_requiring_review"]
    assert "inventory_carrying" in result["missing_warehouse_components"]


def test_unverified_component_cannot_be_promoted():
    costs = cost_rows()
    costs[0]["observation_status"] = "provisional"
    result = review(Database(costs=costs))
    assert result["unverified_cost_line_count"] == 1
    assert "unverified_cost_lines" in result["issues_requiring_review"]


def test_provisional_snapshot_requires_review():
    result = review(Database(snapshots=[{**SNAPSHOT, "evidence_status": "provisional"}]))
    assert "cost_snapshot_provisional" in result["issues_requiring_review"]


def test_draft_price_cannot_be_called_approved():
    result = review(Database(books=[{**book_rows()[0], "status": "draft"}]))
    assert "no_recorded_approved_price" in result["issues_requiring_review"]


@pytest.mark.parametrize("table", ["pricing_cost_snapshots", "pricing_cost_lines", "pricing_price_book"])
def test_source_outage_is_never_an_all_clear(table):
    with pytest.raises(PricingEvidenceUnavailable, match="Pricing source unavailable"):
        review(Database(fail=table))


def test_foreign_snapshot_is_not_found_and_no_cross_tenant_result():
    with pytest.raises(PricingSnapshotNotFound):
        review(Database(snapshots=[{**SNAPSHOT, "company_id": "tenant-B"}]))


def test_cross_tenant_row_even_after_filter_is_rejected():
    class LeakingDatabase(Database):
        def table(self, name):
            q = super().table(name)
            if name == "pricing_price_book":
                q.records = [{**book_rows()[0], "company_id": "tenant-B"}]
                q.filters = []
                def bypass(_field, _value): return q
                q.eq = bypass
            return q
    with pytest.raises(PricingEvidenceUnavailable):
        review(LeakingDatabase())


@pytest.mark.parametrize("field,value", [
    ("accepted_cases", "NaN"), ("accepted_cases", "0"),
    ("currency", "usd"), ("source_revision", ""),
])
def test_invalid_snapshot_is_rejected(field, value):
    with pytest.raises(PricingEvidenceUnavailable):
        review(Database(snapshots=[{**SNAPSHOT, field: value}]))


def test_repeated_expense_ref_is_rejected():
    costs = cost_rows()
    costs[1]["expense_line_ref"] = costs[0]["expense_line_ref"]
    with pytest.raises(PricingEvidenceUnavailable, match="Duplicate"):
        review(Database(costs=costs))


def test_wrong_snapshot_price_book_cannot_be_treated_as_linked():
    bad = {**book_rows()[0], "sku": "OTHER-SKU"}
    with pytest.raises(PricingEvidenceUnavailable):
        review(Database(books=[bad]))


def test_no_truncation_allowed_when_cost_lines_exceed_supported_scope():
    costs = cost_rows()
    costs.extend({**costs[0], "expense_line_ref": f"filler-{x}"} for x in range(210))
    with pytest.raises(PricingEvidenceUnavailable, match="scope"):
        review(Database(costs=costs))


def test_candidate_index_tenant_isolation_and_no_writes():
    db = Database(snapshots=[SNAPSHOT, {**SNAPSHOT, "id": "foreign", "company_id": "tenant-B"}])
    result = list_cost_snapshot_candidates(company_id=COMPANY, db=db, limit=5)
    assert result["state"] == "snapshot_candidates"
    assert len(result["items"]) == 1
    assert result["items"][0]["cost_snapshot_id"] == SNAP
    assert result["price_changes_allowed"] is False
    db.assert_read_only_and_scoped()


def test_candidate_empty_not_a_zero_price_book():
    result = list_cost_snapshot_candidates(company_id=COMPANY, db=Database(snapshots=[]))
    assert result["state"] == "no_pricing_snapshots_recorded"
    assert result["source_certification"] is False
