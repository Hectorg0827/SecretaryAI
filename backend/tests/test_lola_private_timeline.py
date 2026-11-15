"""Private recorded timeline: no credentials, content or false 'no reply'."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app
from app.intelligence.lola.private_timeline import (
    CONTACTS, EVENTS, TimelineNotFound, TimelineUnavailable,
    list_private_case_events,
)
from app.intelligence.lola.identity_registry import IdentityAccessDenied
from test_lola_identity_registry import COMPANY, EMPLOYEE, USER, FakeDB

CONTACT = UUID("123e4567-e89b-12d3-a456-426614174090")
PROJECT = "label-redesign"
BASE_DATE = "2026-11-14T16:00:00+00:00"
EVENT_ID = "223e4567-e89b-12d3-a456-426614174090"


def recorded(**changes):
    return {
        "id": EVENT_ID, "company_id": COMPANY, "user_id": EMPLOYEE,
        "contact_id": str(CONTACT), "project_key": PROJECT,
        "channel": "whatsapp", "direction": "outbound",
        "kind": "draft_sent", "occurred_at": BASE_DATE,
        "origin": "manual", "created_at": BASE_DATE,
        "summary": "PRIVATE label draft text",
        "source_ref": "joan@example.com/message-id",
        **changes,
    }


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters: list[tuple[str, str]] = []
        self.bound = None
        self.range_start = self.range_end = None
        self.order_fields: list[str] = []
    def select(self, fields):
        self.db.calls.append((self.table, "select", fields))
        return self
    def eq(self, field, val):
        self.filters.append((field, str(val)))
        self.db.calls.append((self.table, "eq", field, str(val)))
        return self
    def limit(self, n):
        self.bound = n
        return self
    def order(self, field, *, desc=False):
        self.order_fields.append(field)
        self.db.calls.append((self.table, "order", field, desc))
        return self
    def range(self, start, end):
        self.range_start, self.range_end = start, end
        return self
    def execute(self):
        if self.table == self.db.failure:
            raise RuntimeError("PRIVATE DATABASE SECRET IN EXCEPTION")
        rows = deepcopy(self.db.tables[self.table])
        rows = [row for row in rows if all(str(row.get(f)) == v for f,v in self.filters)]
        if self.order_fields:
            for name in reversed(self.order_fields):
                rows.sort(key=lambda r: str(r.get(name)), reverse=True)
        if self.bound is not None:
            rows = rows[:self.bound]
        if self.range_start is not None:
            rows = rows[self.range_start:self.range_end+1]
        return SimpleNamespace(data=rows)


class TimelineDb(FakeDB):
    def __init__(self):
        super().__init__()
        self.tables[CONTACTS] = [{
            "id": str(CONTACT), "company_id": COMPANY, "user_id": EMPLOYEE,
            "display_name": "PRIVATE CONTACT",
            "email": "private@example.com", "phone": "+10000000123",
        }]
        self.tables[EVENTS] = [recorded()]
        self.failure = None
    def table(self, name):
        if name in (CONTACTS, EVENTS):
            return Query(self, name)
        return super().table(name)


def view(db=None, *, user=USER, contact=CONTACT, project=PROJECT, limit=20, offset=0):
    return list_private_case_events(
        user=user, db=db or TimelineDb(), contact_id=contact,
        project_key=project, limit=limit, offset=offset,
    )


def test_recorded_activity_never_certifies_an_unanswered_message():
    db = TimelineDb()
    result = view(db)
    assert result["state"] == "recorded_events"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["channel"] == "whatsapp"
    assert event["kind"] == "draft_sent"
    assert event["source_class"] == "manual_or_document_record_not_provider_verified"
    assert event["event_ref"].startswith("recorded-")
    assert event["provider_sync_verified"] is False
    assert result["history_completeness"] == "unknown"
    assert result["reply_status"] == "not_assessed"
    assert result["eligible_for_follow_up_engine"] is False
    assert result["messages_sent"] is False


def test_private_content_email_and_phone_are_neither_read_nor_returned():
    db = TimelineDb()
    result = view(db)
    columns = [call[2] for call in db.calls if call[1] == "select"]
    for selection in columns:
        for secret in ("summary", "source_ref", "display_name", "email", "phone", "access_token"):
            assert secret not in selection
    text = str(result)
    for marker in ("PRIVATE", "private@example.com", "+10000000123", "joan@example.com"):
        assert marker not in text


def test_provider_labeled_event_is_not_automatically_verified():
    db = TimelineDb()
    db.tables[EVENTS][0].update({"origin": "whatsapp_api"})
    result = view(db)
    assert result["events"][0]["source_class"] == "provider_labeled_but_unverified_completeness"
    assert result["eligible_for_follow_up_engine"] is False


def test_other_employee_private_event_does_not_leak():
    db = TimelineDb()
    db.tables[EVENTS][0]["user_id"] = "another-employee"
    assert view(db)["events"] == []


def test_other_tenant_contact_cannot_be_searched():
    db = TimelineDb()
    db.tables[CONTACTS][0]["company_id"] = "other-tenant"
    with pytest.raises(TimelineNotFound):
        view(db)
    assert not any(call[0] == EVENTS for call in db.calls)


def test_unknown_private_contact_is_not_an_all_clear():
    db = TimelineDb()
    db.tables[CONTACTS] = []
    with pytest.raises(TimelineNotFound):
        view(db)


def test_wrong_private_work_item_has_no_events_not_a_no_reply_claim():
    result = view(project="another-project")
    assert result["state"] == "no_events_recorded_in_this_page"
    assert result["reply_status"] == "not_assessed"


def test_mismatched_project_row_fails_closed_on_leaky_database():
    db = TimelineDb()
    class LeakyQuery(Query):
        def eq(self, field, val):
            if field == "project_key":
                return self
            return super().eq(field, val)
    class LeakyDb(TimelineDb):
        def table(self, name):
            if name == EVENTS:
                return LeakyQuery(self, name)
            return super().table(name)
    db = LeakyDb()
    with pytest.raises(TimelineUnavailable):
        view(db, project="other-project")


@pytest.mark.parametrize("changes", [
    {"origin": "gmail_api", "channel": "whatsapp"},
    {"channel": "sms"},
    {"kind": "send_payment"},
    {"occurred_at": "2026-11-14T12:00:00"},
    {"occurred_at": "2040-01-01T00:00:00+00:00"},
    {"id": "not-uuid"},
])
def test_malformed_record_not_promoted_to_timeline(changes):
    db = TimelineDb()
    db.tables[EVENTS][0].update(changes)
    with pytest.raises(TimelineUnavailable):
        view(db)


@pytest.mark.parametrize("bad", ["", " ", "a"*161, "bad\nproject"])
def test_invalid_project_key_rejected(bad):
    with pytest.raises(ValueError):
        view(project=bad)


def test_page_bounded_without_complete_history_claim():
    db = TimelineDb()
    db.tables[EVENTS] = [
        recorded(id=f"223e4567-e89b-12d3-a456-{n:012d}")
        for n in range(3)
    ]
    page = view(db, limit=2)
    assert len(page["events"]) == 2
    assert page["next_offset"] == 2
    assert page["history_completeness"] == "unknown"
    last = view(db, limit=2, offset=2)
    assert len(last["events"]) == 1
    assert last["next_offset"] is None


def test_duplicate_event_identity_is_rejected():
    db = TimelineDb()
    db.tables[EVENTS] = [recorded(), recorded()]
    with pytest.raises(TimelineUnavailable):
        view(db)


@pytest.mark.parametrize("source", [CONTACTS, EVENTS])
def test_unavailable_table_is_not_empty_history(source):
    db = TimelineDb()
    db.failure = source
    with pytest.raises(TimelineUnavailable):
        view(db)


def test_revoked_employee_denied_before_private_read():
    db = TimelineDb()
    db.tables["users"][0]["is_active"] = False
    with pytest.raises(IdentityAccessDenied):
        view(db)
    assert not any(call[0] in (CONTACTS, EVENTS) for call in db.calls)


@pytest.fixture
def client():
    db = TimelineDb()
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_api_authenticated_own_recorded_events_only(client):
    web, db = client
    r = web.get(
        f"/api/lola/chief-of-staff/continuity/cases/{CONTACT}/recorded-events",
        params={"project_key": PROJECT},
    )
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    assert r.json()["reply_status"] == "not_assessed"
    assert "PRIVATE label draft text" not in r.text


def test_api_denied_role_403(client):
    web, db = client
    db.tables["users"][0]["role"] = "viewer"
    response = web.get(
        f"/api/lola/chief-of-staff/continuity/cases/{CONTACT}/recorded-events",
        params={"project_key": PROJECT},
    )
    assert response.status_code == 403


def test_api_unavailable_503(client):
    web, db = client
    db.failure = EVENTS
    response = web.get(
        f"/api/lola/chief-of-staff/continuity/cases/{CONTACT}/recorded-events",
        params={"project_key": PROJECT},
    )
    assert response.status_code == 503
    assert "DATABASE SECRET" not in response.text


def test_api_other_employee_404(client):
    web, db = client
    db.tables[CONTACTS][0]["user_id"] = "someone-else"
    response = web.get(
        f"/api/lola/chief-of-staff/continuity/cases/{CONTACT}/recorded-events",
        params={"project_key": PROJECT},
    )
    assert response.status_code == 404
