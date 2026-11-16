"""No fallback from append-only insertion into an UPDATE-style upsert.

These are provider-free unit tests: never write a real shipment or touch Supabase.
"""
import pytest

from app.intelligence.wholesale_distribution.logistics.journey import (
    LogisticsEvent, SourceType, StoreUnavailableError,
)
from app.intelligence.wholesale_distribution.logistics.store import SupabaseEventStore


class Query:
    def __init__(self, reject_ignore=False):
        self.reject_ignore = reject_ignore
        self.calls = []
        self.executed = 0

    def upsert(self, row, *, on_conflict, ignore_duplicates):
        self.calls.append({
            "conflict": on_conflict, "ignore_duplicates": ignore_duplicates,
            "company": row.get("company_id"),
        })
        if self.reject_ignore:
            raise TypeError("legacy SDK unsupported kwarg")
        return self

    def execute(self):
        self.executed += 1
        return type("Result", (), {"data": []})()


class DB:
    def __init__(self, *, reject_ignore=False):
        self.query = Query(reject_ignore=reject_ignore)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.query


def event():
    return LogisticsEvent(
        company_id="test-tenant", shipment_id="test-shipment",
        kind="shipment_created", source=SourceType.ERP,
        payload={"po_numbers": ["test-PO"]},
    )


def test_only_safe_do_nothing_conflict_mode_used_for_new_events():
    db = DB()
    SupabaseEventStore(db).append(event())
    assert db.tables == ["logistics_events"]
    assert db.query.executed == 1
    assert db.query.calls == [{
        "conflict": "company_id,idempotency_key",
        "ignore_duplicates": True,
        "company": "test-tenant",
    }]


def test_legacy_client_must_not_fallback_to_update_style_upsert():
    db = DB(reject_ignore=True)
    with pytest.raises(StoreUnavailableError, match="safe client upgrade"):
        SupabaseEventStore(db).append(event())
    # There was exactly one safe attempt and zero accepted writes; most
    # importantly there is no second default/updating upsert attempt.
    assert db.query.executed == 0
    assert db.tables == ["logistics_events"]
    assert len(db.query.calls) == 1
    assert db.query.calls[0]["ignore_duplicates"] is True
