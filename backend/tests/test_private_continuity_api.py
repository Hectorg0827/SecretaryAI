"""Service-role bypass regression: private queries MUST constrain employee ID."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.private_continuity import _contact, private_followups


class Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.sort_field = None
        self.desc = False
        self.n = None

    def select(self, *args):
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def gte(self, field, value):
        self.filters.append(("gte", field, value))
        return self

    def order(self, field, desc=False):
        self.sort_field, self.desc = field, desc
        return self

    def limit(self, n):
        self.n = n
        return self

    def execute(self):
        rows = self.rows
        for op, field, value in self.filters:
            if op == "eq":
                rows = [r for r in rows if str(r.get(field)) == str(value)]
            elif op == "gte":
                rows = [r for r in rows if str(r.get(field)) >= str(value)]
        if self.sort_field:
            rows = sorted(rows, key=lambda r: r[self.sort_field], reverse=self.desc)
        if self.n is not None:
            rows = rows[: self.n]
        return SimpleNamespace(data=rows)


class DB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return Query(self.tables.get(name, []))


def test_private_due_queue_excludes_other_employee_even_under_same_company():
    company_id, employee_a, employee_b = str(uuid4()), str(uuid4()), str(uuid4())
    contact_a, contact_b = str(uuid4()), str(uuid4())
    when = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    db = DB(
        lola_private_contacts=[
            {"id": contact_a, "company_id": company_id, "user_id": employee_a,
             "display_name": "Joan", "email": "joan@example.test", "phone": "+15550000001"},
            {"id": contact_b, "company_id": company_id, "user_id": employee_b,
             "display_name": "Joan", "email": "joan@example.test", "phone": "+15550000001"},
        ],
        lola_private_events=[
            {"id": str(uuid4()), "company_id": company_id, "user_id": employee_a,
             "contact_id": contact_a, "project_key": "label", "channel": "whatsapp",
             "direction": "outbound", "kind": "draft_sent", "occurred_at": when},
            {"id": str(uuid4()), "company_id": company_id, "user_id": employee_b,
             "contact_id": contact_b, "project_key": "pricing", "channel": "email",
             "direction": "outbound", "kind": "quote_sent", "occurred_at": when},
        ],
        lola_private_followup_state=[],
    )
    a = private_followups(user={"company_id": company_id, "sub": employee_a, "role": "sales_rep"}, db=db)
    b = private_followups(user={"company_id": company_id, "sub": employee_b, "role": "owner"}, db=db)
    assert len(a["followups"]) == 1 and a["followups"][0]["project_key"] == "label"
    assert len(b["followups"]) == 1 and b["followups"][0]["project_key"] == "pricing"
    with pytest.raises(HTTPException) as denied:
        _contact(db, {"company_id": company_id, "sub": employee_b}, contact_a)
    assert denied.value.status_code == 404


def test_missing_user_identity_fails_closed():
    with pytest.raises(HTTPException) as denied:
        private_followups(user={"company_id": str(uuid4()), "role": "owner"}, db=DB())
    assert denied.value.status_code == 401


def test_chat_private_context_loads_only_verified_owned_contact_events():
    from app.api.private_continuity import private_context_for_query
    company_id, employee_a, employee_b = str(uuid4()), str(uuid4()), str(uuid4())
    contact_a, contact_b = str(uuid4()), str(uuid4())
    base = {
        "company_id": company_id,
        "display_name": "Joan", "email": "joan@example.test",
        "identity_verified": True,
    }
    db = DB(
        lola_private_contacts=[
            dict(base, id=contact_a, user_id=employee_a),
            dict(base, id=contact_b, user_id=employee_b),
        ],
        lola_private_events=[
            {
                "id": str(uuid4()), "company_id": company_id, "user_id": employee_a,
                "contact_id": contact_a, "project_key": "label-artwork",
                "channel": "whatsapp", "direction": "outbound",
                "kind": "draft_sent", "origin": "manual",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "summary": "Draft sent by me",
            },
            {
                "id": str(uuid4()), "company_id": company_id, "user_id": employee_b,
                "contact_id": contact_b, "project_key": "private-pricing",
                "channel": "email", "direction": "inbound",
                "kind": "request", "origin": "gmail_api",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "summary": "Confidential to employee B",
            },
        ],
    )
    a = private_context_for_query(db, {"company_id": company_id, "sub": employee_a}, "What happened with Joan?")
    b = private_context_for_query(db, {"company_id": company_id, "sub": employee_b, "role": "owner"}, "What happened with Joan?")
    assert len(a) == len(b) == 1
    assert a[0]["project"] == "label-artwork"
    assert b[0]["project"] == "private-pricing"
    assert "Confidential" not in str(a)
    assert "Draft sent" not in str(b)


def test_chat_context_ambiguous_contact_name_does_not_merge_histories():
    from app.api.private_continuity import private_context_for_query
    company_id, employee = str(uuid4()), str(uuid4())
    db = DB(lola_private_contacts=[
        {"id": str(uuid4()), "company_id": company_id, "user_id": employee,
         "display_name": "Joan", "identity_verified": True},
        {"id": str(uuid4()), "company_id": company_id, "user_id": employee,
         "display_name": "Joan", "identity_verified": True},
    ])
    assert private_context_for_query(db, {"company_id": company_id, "sub": employee}, "Any updates from Joan?") == []
