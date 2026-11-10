"""Gate 4: staged purchasing coverage is read-only, scoped, and never an all-clear."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.briefing import BriefingSourceUnavailable, build_owner_briefing
from app.domain.briefing_wire import BriefingTodayResponseV1
from app.scheduler.dashboard_snapshot import (
    SnapshotDataInvalid, validate_dashboard_cached_payload,
)
from app.scheduler.purchasing_coverage import (
    PurchasingCoverageInvalid, read_purchasing_coverage,
    unavailable_purchasing_coverage, validate_purchasing_coverage,
)
from tasks.dashboard_refresh import refresh_company_snapshot

NOW = datetime(2026, 11, 10, 5, tzinfo=timezone.utc)
COMPANY = "tenant-a"


class Query:
    def __init__(self, rows, audit, table):
        self.rows, self.audit, self.table = rows, audit, table

    def select(self, fields):
        self.audit.append((self.table, "select", fields))
        return self

    def eq(self, field, value):
        self.audit.append((self.table, "eq", field, value))
        return self

    def limit(self, value):
        self.audit.append((self.table, "limit", value))
        return self

    def execute(self):
        self.audit.append((self.table, "execute"))
        if isinstance(self.rows, Exception):
            raise self.rows
        return SimpleNamespace(data=self.rows)


class DB:
    def __init__(self, orders, receipts):
        self.tables = {"purchasing_orders": orders, "staged_warehouse_receipts": receipts}
        self.audit = []

    def table(self, name):
        self.audit.append((name, "table"))
        return Query(self.tables[name], self.audit, name)


def snapshot(purchasing=None):
    payload = {
        "source_read_status": "completed", "source_quality": "partial",
        "accounts": {"healthy": 1, "slowing": 0, "at_risk": 0,
                     "dormant": 0, "unknown": 0, "total": 1},
        "sales_30d": 20.0, "inventory_alerts": [],
    }
    if purchasing is not None:
        payload["purchasing_coverage"] = purchasing
    return {"generated_at": NOW.isoformat(), "payload": payload}


def test_read_is_tenant_scoped_bounded_and_aggregate_only():
    db = DB([{"id": "secret-po-id", "status": "draft", "company_id": COMPANY}], [])
    result = read_purchasing_coverage(db, COMPANY)
    assert result == {
        "status": "observed", "source_quality": "partial", "reason": None,
        "purchase_orders_by_status": {"draft": 1, "approved": 0,
                                      "partially_received": 0, "received": 0},
        "purchase_orders_total": 1, "pending_receipts": 0,
    }
    assert ("purchasing_orders", "select", "id,status,company_id") in db.audit
    assert ("staged_warehouse_receipts", "select", "id,company_id") in db.audit
    assert ("purchasing_orders", "eq", "company_id", COMPANY) in db.audit
    assert ("staged_warehouse_receipts", "eq", "company_id", COMPANY) in db.audit
    assert ("staged_warehouse_receipts", "eq", "status", "pending") in db.audit
    assert all(item[1] not in ("insert", "upsert", "update", "delete") for item in db.audit)
    assert "secret-po-id" not in str(result)


@pytest.mark.parametrize("orders,receipts", [
    (RuntimeError("secret connection string"), []),
    ([], RuntimeError("secret supplier reference")),
    (None, []),
    ([{"id": "x", "status": "arbitrary", "company_id": COMPANY}], []),
    ([{"id": "x", "status": "draft", "company_id": COMPANY},
      {"id": "x", "status": "approved", "company_id": COMPANY}], []),
    ([], [{"id": None, "company_id": COMPANY}]),
    ([{"id": "x", "status": "draft", "company_id": "other-tenant"}], []),
    ([], [{"id": "x", "company_id": "other-tenant"}]),
    ([{"id": str(i), "status": "draft", "company_id": COMPANY} for i in range(501)], []),
])
def test_failed_or_unbounded_reads_are_unavailable_not_observed_zero(orders, receipts):
    value = read_purchasing_coverage(DB(orders, receipts), COMPANY)
    assert value == unavailable_purchasing_coverage()
    assert "secret" not in str(value)


@pytest.mark.parametrize("change", [
    lambda x: x.update(purchase_orders_total=0),
    lambda x: x.update(pending_receipts=-1),
    lambda x: x.update(pending_receipts=True),
    lambda x: x["purchase_orders_by_status"].update(draft="1"),
    lambda x: x.update(source_quality="verified"),
    lambda x: x.update(unexpected_supplier_record="sensitive"),
])
def test_corrupt_cache_rejected_before_briefing(change):
    observed = read_purchasing_coverage(DB([{"id": "x", "status": "draft", "company_id": COMPANY}], []), COMPANY)
    change(observed)
    with pytest.raises(PurchasingCoverageInvalid):
        validate_purchasing_coverage(observed)
    with pytest.raises(SnapshotDataInvalid):
        validate_dashboard_cached_payload(snapshot(observed)["payload"])
    with pytest.raises(BriefingSourceUnavailable):
        build_owner_briefing(company_id=COMPANY, role="owner", snapshot=snapshot(observed), now=NOW)


def test_observed_logistics_appears_in_executive_digest_without_identifiers():
    observed = read_purchasing_coverage(DB([{"id": "secret-id", "status": "draft", "company_id": COMPANY}], []), COMPANY)
    result = build_owner_briefing(company_id=COMPANY, role="owner", snapshot=snapshot(observed), now=NOW)
    assert result["source_coverage"] == "dashboard_snapshot_plus_purchasing"
    assert result["complete_digest"] is False
    assert BriefingTodayResponseV1.model_validate(result).complete_digest is False
    assert any("1 recorded orders" in item["title"] for item in result["sections"]["scoreboard"])
    assert any("Freight, customs" in item["title"] for item in result["sections"]["yellow_alerts"])
    assert "secret-id" not in str(result)
    assert "supplier_ref" not in str(result)


def test_unavailable_logistics_creates_unavailable_warning_not_zero_activity():
    result = build_owner_briefing(
        company_id=COMPANY, role="manager",
        snapshot=snapshot(unavailable_purchasing_coverage()), now=NOW,
    )
    warnings = result["sections"]["yellow_alerts"]
    assert any(item["status"] == "source_unavailable" for item in warnings)
    assert not any("0 recorded orders" in item["title"] for item in result["sections"]["scoreboard"])
    with pytest.raises(PermissionError):
        build_owner_briefing(company_id=COMPANY, role="sales_rep", snapshot=snapshot(
            unavailable_purchasing_coverage()), now=NOW)


def test_only_explicit_staging_opt_in_adds_purchasing_source():
    db = MagicMock()
    adapter = MagicMock()
    companies = [{"id": COMPANY}]
    payload = snapshot()["payload"]
    with (patch("tasks.dashboard_refresh.compute_dashboard_payload", return_value=payload),
          patch("tasks.dashboard_refresh.store_snapshot") as store,
          patch("tasks.dashboard_refresh.read_purchasing_coverage", return_value=unavailable_purchasing_coverage()) as read):
        refresh_company_snapshot(COMPANY, db=db, companies=companies,
                                 adapter_factory=lambda _: adapter)
        assert "purchasing_coverage" not in store.call_args.args[3]
        read.assert_not_called()
        refresh_company_snapshot(COMPANY, db=db, companies=companies,
                                 adapter_factory=lambda _: adapter,
                                 include_purchasing_coverage=True)
        assert store.call_args.args[3]["purchasing_coverage"] == unavailable_purchasing_coverage()
        read.assert_called_once_with(db, COMPANY)
