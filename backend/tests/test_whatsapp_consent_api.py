"""Employee private WhatsApp consent requests: no Meta connection or token is created."""
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.private_continuity import (
    WhatsAppConsent, whatsapp_readiness,
    request_private_whatsapp_connection, revoke_private_whatsapp_consent,
)


class FakeQuery:
    def __init__(self, db):
        self.db = db
        self.filters = []
        self.command = "read"
        self.payload = None

    def select(self, *_args): return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def limit(self, _size): return self

    def upsert(self, payload, **_kwargs):
        self.payload = payload
        self.command = "upsert"
        return self

    def update(self, payload):
        self.payload = payload
        self.command = "update"
        return self

    def execute(self):
        from types import SimpleNamespace
        rows = [row for row in self.db.rows if all(row.get(k) == v for k, v in self.filters)]
        if self.command == "upsert":
            self.db.rows = [
                row for row in self.db.rows
                if not (row["company_id"] == self.payload["company_id"]
                        and row["user_id"] == self.payload["user_id"])
            ]
            self.db.rows.append(self.payload.copy())
            return SimpleNamespace(data=[self.payload])
        if self.command == "update":
            for row in rows:
                row.update(self.payload)
        return SimpleNamespace(data=rows)


class FakeDB:
    def __init__(self):
        self.rows = []

    def table(self, table):
        assert table == "lola_employee_whatsapp_connections"
        return FakeQuery(self)


def test_joan_pilot_employee_only_consent_then_revoke():
    db = FakeDB()
    company = str(uuid4())
    owner = {"company_id": company, "sub": str(uuid4())}
    manager = {"company_id": company, "sub": str(uuid4()), "role": "owner"}
    before = whatsapp_readiness(user=owner, db=db)
    assert before["status"] == "not_requested"
    assert not before["can_launch_embedded_signup"]
    assert not before["can_sync_messages"]
    assert not before["can_send_messages"]

    consent = request_private_whatsapp_connection(
        WhatsAppConsent(consent=True, allow_one_time_history_sync=False),
        user=owner, db=db,
    )
    assert consent["connected"] is False
    assert consent["employee_consented"] is True
    assert db.rows[0]["user_id"] == owner["sub"]
    assert db.rows[0]["phone_number_id"] is None

    assert whatsapp_readiness(user=manager, db=db)["status"] == "not_requested"
    assert whatsapp_readiness(user=owner, db=db)["employee_consented"]

    revoked = revoke_private_whatsapp_consent(user=owner, db=db)
    assert revoked["consent_revoked"]
    assert whatsapp_readiness(user=owner, db=db)["status"] == "disconnected"
    assert not whatsapp_readiness(user=owner, db=db)["employee_consented"]


def test_employee_cannot_self_verify_meta_ownership_or_policy_clearance():
    db = FakeDB()
    user = {"company_id": str(uuid4()), "sub": str(uuid4())}
    request_private_whatsapp_connection(
        WhatsAppConsent(consent=True, allow_one_time_history_sync=True),
        user=user, db=db,
    )
    row = db.rows[0]
    assert row["status"] == "pending_meta_approval"
    assert row["meta_verified_at"] is None
    assert row["policy_clearance_id"] is None
    assert row["waba_id"] is None
    assert row["phone_number_id"] is None


def test_no_consent_no_connection_and_no_foreign_revoke():
    db = FakeDB()
    user = {"company_id": str(uuid4()), "sub": str(uuid4())}
    other = {"company_id": user["company_id"], "sub": str(uuid4())}
    with pytest.raises(HTTPException) as blocked:
        request_private_whatsapp_connection(
            WhatsAppConsent(consent=False), user=user, db=db,
        )
    assert blocked.value.status_code == 422
    assert db.rows == []
    request_private_whatsapp_connection(WhatsAppConsent(consent=True), user=user, db=db)
    revoke_private_whatsapp_consent(user=other, db=db)
    assert db.rows[0]["user_id"] == user["sub"]
    assert db.rows[0]["employee_revoked_at"] is None


def test_schema_denies_public_reads_and_requires_unique_phone_owner():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "supabase" / "migrations" /
           "20261113234500_lola_whatsapp_consent_and_ownership.sql").read_text()
    assert "UNIQUE (company_id, user_id)" in sql
    assert "ON lola_employee_whatsapp_connections(phone_number_id)" in sql
    assert "WHERE phone_number_id IS NOT NULL" in sql
    assert "FOREIGN KEY (company_id, user_id)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON lola_employee_whatsapp_connections FROM anon, authenticated" in sql
