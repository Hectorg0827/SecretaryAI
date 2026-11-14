"""Staging-safe event-store paging regression tests, no live database writes."""
import pytest

from app.intelligence.wholesale_distribution.logistics import store
from app.intelligence.wholesale_distribution.logistics.journey import (
    LogisticsEvent, SourceType, StoreUnavailableError,
)


def event_rows(company, count):
    return [
        {**LogisticsEvent(company_id=company, shipment_id=f"SHIP-{i}",
                         kind="shipment_created", source=SourceType.ERP,
                         recorded_at="2026-11-14T12:00:00Z",
                         event_id=f"event-{i:06d}").to_dict(),
         "inserted_at": "2026-11-14T12:00:01Z"}
        for i in range(count)
    ]


class PagedTable:
    def __init__(self, db):
        self.db = db
        self.tenant = None
        self.slice = None

    def select(self, *args):
        return self

    def eq(self, col, value):
        assert col == "company_id"  # every page requires tenant scoping
        self.tenant = value
        return self

    def order(self, column, desc=False):
        assert column in ("recorded_at", "inserted_at", "event_id")
        assert desc is False
        return self

    def range(self, start, end):
        self.slice = (start, end)
        return self

    def execute(self):
        assert self.tenant is not None
        assert self.slice is not None
        start, end = self.slice
        self.db.requests.append((self.tenant, start, end))
        if self.db.fail_page == start:
            raise ConnectionError("Synthetic page transport failure")
        if self.db.bad_response_at == start:
            return type("Result", (), {"data": None})()
        source = self.db.rows
        if self.db.filter_tenant:
            source = [row for row in source if row.get("company_id") == self.tenant]
        ordered = sorted(source, key=lambda x: (
            x.get("recorded_at", ""), x.get("inserted_at", ""), x.get("event_id", "")))
        return type("Result", (), {"data": ordered[start:end + 1]})()


class PagedDB:
    def __init__(self, rows, *, fail_page=None, bad_response_at=None, filter_tenant=True):
        self.rows = rows
        self.fail_page = fail_page
        self.bad_response_at = bad_response_at
        self.filter_tenant = filter_tenant
        self.requests = []

    def table(self, name):
        assert name == "logistics_events"
        return PagedTable(self)


def test_loading_over_one_thousand_events_is_complete_and_tenant_scoped():
    db = PagedDB(event_rows("company-A", 1001) + event_rows("company-B", 37))
    output = store.SupabaseEventStore(db).load("company-A")
    assert len(output) == 1001
    assert {x.company_id for x in output} == {"company-A"}
    assert output[0].event_id == "event-000000"
    assert output[-1].event_id == "event-001000"
    assert db.requests == [("company-A", 0, 499), ("company-A", 500, 999),
                           ("company-A", 1000, 1499)]


def test_second_page_failure_is_not_silently_returned_as_partial_ledger():
    db = PagedDB(event_rows("company-A", 1100), fail_page=500)
    with pytest.raises(StoreUnavailableError, match="unavailable"):
        store.SupabaseEventStore(db).load("company-A")
    assert db.requests == [("company-A", 0, 499), ("company-A", 500, 999)]


def test_invalid_response_is_not_misreported_as_empty_company_ledger():
    db = PagedDB(event_rows("company-A", 1), bad_response_at=0)
    with pytest.raises(StoreUnavailableError, match="Invalid"):
        store.SupabaseEventStore(db).load("company-A")


def test_tenant_mixing_is_rejected_even_if_provider_ignores_filter():
    db = PagedDB(event_rows("company-A", 1) + event_rows("company-B", 1),
                 filter_tenant=False)
    with pytest.raises(StoreUnavailableError, match="tenant"):
        store.SupabaseEventStore(db).load("company-A")


def test_malformed_event_fails_closed_not_skipped():
    rows = event_rows("company-A", 1)
    rows[0]["source"] = "not_a_valid_source"
    with pytest.raises(StoreUnavailableError, match="Malformed"):
        store.SupabaseEventStore(PagedDB(rows)).load("company-A")


def test_page_limit_fails_closed_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(store, "MAX_EVENTS_PER_COMPANY", 1000)
    db = PagedDB(event_rows("company-A", 1001))
    with pytest.raises(StoreUnavailableError, match="limit"):
        store.SupabaseEventStore(db).load("company-A")
