"""Synthetic, no-network Lola review journal security and idempotence tests."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app
from app.intelligence.lola import review_journal as j

COMPANY = "tenant-a"
OTHER = "tenant-b"
ACTOR = "operator-a"
REVIEW_ID = UUID("123e4567-e89b-12d3-a456-426614174080")


@dataclass
class FakeAlert:
    alert_id: str = "alr-test"
    shipment_id: str = "shipment-a"
    code: str = "CUSTOMS_HOLD"
    severity: str = "critical"

    def to_dict(self):
        return dict(self.__dict__)


class FakeLedger:
    def __init__(self, company_id=COMPANY, *, event_key="source-1"):
        self.company_id = company_id
        self._shipment = SimpleNamespace(shipment_id="shipment-a", company_id=company_id)
        self._events = [
            SimpleNamespace(company_id=company_id, shipment_id="shipment-a",
                            idempotency_key=event_key),
        ]
    def shipments(self):
        return [self._shipment]
    def events(self):
        return self._events


class FakeViewer:
    def __init__(self, user): self.user = user
    @classmethod
    def from_user(cls, user): return cls(user)
    def require_access(self): return None
    def filter_alerts(self, alerts, shipments): return [a.to_dict() for a in alerts]


class FakeMonitor:
    alerts = [FakeAlert()]
    def __init__(self, ledger): self.ledger = ledger
    def scan(self, as_of): return list(self.alerts)


class FakeQuery:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters = []
        self.n = None
        self.start, self.end = None, None
        self.update_data = None
        self.upsert_data = None
        self.kind = "select"
    def select(self, fields):
        self.db.calls.append((self.name, "select", fields))
        return self
    def eq(self, key, value):
        self.filters.append((key, str(value)))
        self.db.calls.append((self.name, "eq", key, str(value)))
        return self
    def order(self, key, desc=False):
        self.db.calls.append((self.name, "order", key, desc))
        return self
    def limit(self, n):
        self.n = n
        return self
    def range(self, start, end):
        self.start, self.end = start, end
        return self
    def upsert(self, records, *, on_conflict, ignore_duplicates):
        assert self.name == j.TABLE
        assert on_conflict == "company_id,event_key" and ignore_duplicates is True
        self.kind = "upsert"
        self.upsert_data = records
        self.db.calls.append((self.name, "upsert"))
        return self
    def update(self, data):
        assert self.name == j.TABLE
        self.kind = "update"
        self.update_data = data
        self.db.calls.append((self.name, "update"))
        return self
    def execute(self):
        if self.name == self.db.failing_table:
            raise ConnectionError("PRIVATE secret provider body")
        if self.kind == "upsert":
            inserted = []
            for record in self.upsert_data:
                assert record["company_id"] == COMPANY
                assert record["review_state"] == "pending"
                if not any(e["company_id"] == record["company_id"] and
                           e["event_key"] == record["event_key"] for e in self.db.events):
                    full = {**record, "id": str(REVIEW_ID) if not self.db.events
                            else "223e4567-e89b-12d3-a456-426614174080",
                            "first_seen_at": "2026-11-15T03:00:00Z",
                            "acknowledged_at": None, "acknowledged_by": None}
                    self.db.events.append(full)
                    inserted.append(full)
            return SimpleNamespace(data=inserted)
        records = self.db.employee_rows if self.name == "users" else self.db.events
        chosen = [dict(row) for row in records if all(
            str(row.get(field)) == value for field, value in self.filters
        )]
        if self.kind == "update":
            if self.db.concurrent_claim:
                return SimpleNamespace(data=[])
            for row in self.db.events:
                if any(row["id"] == r["id"] for r in chosen):
                    row.update(self.update_data)
            return SimpleNamespace(data=[{**r, **self.update_data} for r in chosen])
        if self.start is not None:
            chosen = chosen[self.start:self.end+1]
        elif self.n is not None:
            chosen = chosen[:self.n]
        return SimpleNamespace(data=chosen)


class FakeDatabase:
    def __init__(self, *, role="owner", active=True, company=COMPANY,
                 failing_table=None, concurrent_claim=False):
        self.employee_rows = [{"id": ACTOR, "company_id": company,
                               "role": role, "is_active": active}]
        self.events = []
        self.calls = []
        self.failing_table = failing_table
        self.concurrent_claim = concurrent_claim
    def table(self, name):
        assert name in {"users", j.TABLE}
        return FakeQuery(self, name)


def user(role="owner", company=COMPANY):
    return {"sub": ACTOR, "company_id": company, "role": role}


@pytest.fixture(autouse=True)
def fake_monitor(monkeypatch):
    monkeypatch.setattr(j, "LogisticsViewer", FakeViewer)
    monkeypatch.setattr(j, "LogisticsMonitor", FakeMonitor)
    FakeMonitor.alerts = [FakeAlert()]


def capture(db, ledger=None):
    return j.capture_reviews(user=user(), db=db, ledger=ledger or FakeLedger(),
                             as_of=date.today())


def test_repeated_capture_is_deduplicated_and_has_no_external_side_effects():
    db = FakeDatabase()
    first = capture(db)
    second = capture(db)
    assert len(db.events) == 1
    assert first["candidate_records"] == second["candidate_records"] == 1
    assert first["approval_created"] is False
    assert first["external_writes_executed"] is False
    assert first["notifications_sent"] is False
    assert all(name in {"users", j.TABLE} for name, *_ in db.calls)


def test_new_source_revision_gets_new_pending_event():
    db = FakeDatabase()
    capture(db)
    prior = db.events[0]["event_key"]
    capture(db, FakeLedger(event_key="source-2"))
    assert len(db.events) == 2
    assert db.events[0]["event_key"] == prior
    assert db.events[1]["event_key"] != prior


def test_repeat_capture_never_resets_human_acknowledgement():
    db = FakeDatabase()
    capture(db)
    revision = db.events[0]["source_revision"]
    result = j.acknowledge_review(
        user=user(), db=db, review_id=REVIEW_ID, expected_revision=revision)
    assert result["review_state"] == "acknowledged"
    capture(db)
    assert len(db.events) == 1
    assert db.events[0]["review_state"] == "acknowledged"
    assert db.events[0]["acknowledged_by"] == ACTOR


def test_acknowledgement_is_idempotent_without_executing_action():
    db = FakeDatabase()
    capture(db)
    rev = db.events[0]["source_revision"]
    j.acknowledge_review(user=user(), db=db, review_id=REVIEW_ID, expected_revision=rev)
    again = j.acknowledge_review(user=user(), db=db, review_id=REVIEW_ID, expected_revision=rev)
    assert again["already_acknowledged"] is True
    assert again["external_writes_executed"] is False


def test_source_revision_mismatch_fails_closed():
    db = FakeDatabase()
    capture(db)
    with pytest.raises(j.ReviewConflict):
        j.acknowledge_review(user=user(), db=db, review_id=REVIEW_ID, expected_revision="a" * 64)
    assert db.events[0]["review_state"] == "pending"


def test_losing_concurrent_acknowledgement_is_conflict():
    db = FakeDatabase(concurrent_claim=True)
    capture(db)
    with pytest.raises(j.ReviewConflict):
        j.acknowledge_review(
            user=user(), db=db, review_id=REVIEW_ID,
            expected_revision=db.events[0]["source_revision"])


def test_cross_tenant_review_is_not_visible_or_mutable():
    db = FakeDatabase()
    capture(db)
    db.events[0]["company_id"] = OTHER
    assert j.list_reviews(user=user(), db=db)["items"] == []
    with pytest.raises(j.ReviewRecordNotFound):
        j.acknowledge_review(
            user=user(), db=db, review_id=REVIEW_ID,
            expected_revision=db.events[0]["source_revision"])


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer", "pricing_billbacks"])
def test_restricted_roles_denied_before_source_reads(role):
    db = FakeDatabase(role=role)
    with pytest.raises(j.ReviewJournalDenied):
        j.list_reviews(user=user(role), db=db)
    assert db.calls == []


@pytest.mark.parametrize("change", [
    {"role": "viewer"}, {"active": False}, {"company": OTHER},
])
def test_stale_role_or_disabled_employee_denied(change):
    db = FakeDatabase(**change)
    with pytest.raises(j.ReviewJournalDenied):
        j.list_reviews(user=user(), db=db)
    assert not any(name == j.TABLE for name, *_ in db.calls)


def test_employee_source_failure_never_grants_access():
    db = FakeDatabase(failing_table="users")
    with pytest.raises(j.ReviewJournalUnavailable):
        capture(db)
    assert not any(name == j.TABLE for name, *_ in db.calls)


def test_missing_migration_is_503_not_zero_records():
    db = FakeDatabase(failing_table=j.TABLE)
    with pytest.raises(j.ReviewJournalUnavailable):
        j.list_reviews(user=user(), db=db)


def test_current_scan_has_no_unscoped_supplier_analytics():
    db = FakeDatabase()
    FakeMonitor.alerts = [FakeAlert(), FakeAlert(alert_id="supplier-A",
                                               shipment_id="", code="SUPPLIER_PERFORMANCE")]
    result = capture(db)
    assert result["skipped_unscoped_alerts"] == 1
    assert len(db.events) == 1


def test_mismatched_logistics_tenant_does_not_store():
    db = FakeDatabase()
    with pytest.raises(j.ReviewJournalUnavailable):
        capture(db, FakeLedger(company_id=OTHER))
    assert not any(call[1] == "upsert" for call in db.calls)


def test_untrusted_event_key_blocks_entire_batch():
    db = FakeDatabase()
    ledger = FakeLedger()
    ledger._events[0].idempotency_key = ""
    with pytest.raises(j.ReviewJournalUnavailable):
        capture(db, ledger)
    assert not any(call[1] == "upsert" for call in db.calls)


def test_capture_over_limit_is_not_partial():
    db = FakeDatabase()
    FakeMonitor.alerts = [FakeAlert(alert_id=f"alr-{i}") for i in range(j.MAX_CAPTURE_ALERTS + 1)]
    with pytest.raises(j.ReviewJournalUnavailable):
        capture(db)
    assert not any(call[1] == "upsert" for call in db.calls)


def test_pending_and_all_listing_only_returns_tenant_rows():
    db = FakeDatabase()
    capture(db)
    result = j.list_reviews(user=user(), db=db)
    assert len(result["items"]) == 1
    assert result["items"][0]["alert_code"] == "CUSTOMS_HOLD"
    assert "company_id" not in result["items"][0]
    assert result["external_writes_executed"] is False
    assert j.list_reviews(user=user(), db=db, pending_only=True)["items"]


@pytest.fixture
def client(monkeypatch):
    from app.api import logistics
    # The authenticated fixture substitutes a prebuilt, tenant-local ledger;
    # it may never open a live connector or a Supabase project.
    monkeypatch.setattr(logistics, "_load_ledger", lambda user, db: FakeLedger())
    db = FakeDatabase()
    app.dependency_overrides[get_current_user] = lambda: user()
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_api_human_capture_and_acknowledgement(client):
    web, db = client
    body = web.post("/api/lola/chief-of-staff/reviews/capture")
    assert body.status_code == 200, body.text
    assert body.json()["approval_created"] is False
    listed = web.get("/api/lola/chief-of-staff/reviews")
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "private, no-store"
    row = listed.json()["items"][0]
    reply = web.post(
        "/api/lola/chief-of-staff/reviews/" + row["id"] + "/acknowledge",
        json={"expected_source_revision": row["source_revision"]},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["review_state"] == "acknowledged"
    assert reply.json()["external_writes_executed"] is False
    assert not web.get("/api/lola/chief-of-staff/reviews?pending_only=true").json()["items"]


def test_api_denied_user_cannot_trigger_scan(client):
    web, db = client
    db.employee_rows[0]["role"] = "viewer"
    response = web.post("/api/lola/chief-of-staff/reviews/capture")
    assert response.status_code == 403
    assert db.events == []


def test_api_source_unavailable_never_reports_zero_reviews(client):
    web, db = client
    db.failing_table = j.TABLE
    res = web.get("/api/lola/chief-of-staff/reviews")
    assert res.status_code == 503
    assert "PRIVATE secret" not in res.text


def test_api_invalid_revision_and_uuid_rejected(client):
    web, db = client
    assert web.post(
        f"/api/lola/chief-of-staff/reviews/{REVIEW_ID}/acknowledge",
        json={"expected_source_revision": "not-valid"},
    ).status_code == 422
    assert web.post(
        "/api/lola/chief-of-staff/reviews/not-a-uuid/acknowledge",
        json={"expected_source_revision": "a" * 64},
    ).status_code == 422
    assert db.events == []
