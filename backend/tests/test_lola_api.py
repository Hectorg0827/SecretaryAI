"""Lola HTTP surface + the legacy /api/chat record-level repair."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lola_fakes import CO_A, CO_B, FINANCE, INACTIVE, OWNER, REP1, REP2, REP3, FakeAdapter, seed

from app.api.deps import get_db
from app.auth.jwt import create_access_token
from app.main import app


def token(uid, role="owner", company=CO_A):
    return {"Authorization": "Bearer " + create_access_token({"sub": uid, "company_id": company, "role": role})}


@pytest.fixture
def db():
    d = seed()
    app.dependency_overrides[get_db] = lambda: d
    with patch("app.api.lola._adapter_for", lambda user, db: FakeAdapter()):
        yield d
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


class TestAsk:
    def test_ask_returns_contract(self, db, client):
        r = client.post("/api/lola/ask", json={"message": "show my customers"}, headers=token(REP1, "sales_rep"))
        assert r.status_code == 200
        body = r.json()
        assert body["contract_version"] and body["status"] == "answered"
        names = [a["name"] for a in body["tool_results"][0]["data"]["accounts"]]
        assert names == ["Acme Foods", "Delta Supply"]

    def test_forged_owner_role_in_token_is_ignored(self, db, client):
        r = client.post("/api/lola/ask", json={"message": "list all customers"}, headers=token(REP1, "owner"))
        names = [a["name"] for a in r.json()["tool_results"][0]["data"]["accounts"]]
        assert names == ["Acme Foods", "Delta Supply"]

    def test_client_cannot_supply_tenant_or_user(self, db, client):
        r = client.post("/api/lola/ask", json={"message": "hi", "company_id": CO_B, "user_id": OWNER},
                        headers=token(REP1, "sales_rep"))
        assert r.status_code == 422

    def test_inactive_user_gets_403(self, db, client):
        r = client.post("/api/lola/ask", json={"message": "hi"}, headers=token(INACTIVE, "sales_rep"))
        assert r.status_code == 403

    def test_permission_store_outage_gets_503(self, db, client):
        db.fail.add("users")
        r = client.post("/api/lola/ask", json={"message": "hi"}, headers=token(OWNER))
        assert r.status_code == 503

    def test_unauthenticated_is_rejected(self, db, client):
        assert client.post("/api/lola/ask", json={"message": "hi"}).status_code == 401

    def test_me_reflects_database_role(self, db, client):
        r = client.get("/api/lola/me", headers=token(REP1, "owner"))
        body = r.json()
        assert body["context"]["role"] == "sales_rep"
        perms = {p["resource_type"]: p["allowed"] for p in body["permissions"]}
        assert perms["customer_account"] is True and perms["receivables"] is False and perms["payroll"] is False

    def test_contracts_and_tools_are_published(self, db, client):
        c = client.get("/api/lola/contracts", headers=token(REP1)).json()
        assert {"LolaRequest", "LolaResponse", "ActionReceipt", "PermissionDecision"} <= set(c["schemas"])
        assert {"answered", "partial", "denied", "unavailable", "pending_approval", "completed",
                "failed"} <= set(c["examples"])
        t = client.get("/api/lola/tools", headers=token(REP1)).json()
        assert "finance.receivables" in [x["name"] for x in t["tools"]]


class TestMemoryApi:
    def test_memory_round_trip_and_isolation(self, db, client):
        r = client.post("/api/lola/memory", json={"content": "I like short answers"}, headers=token(REP1))
        assert r.status_code == 201
        mine = client.get("/api/lola/memory", headers=token(REP1)).json()["memories"]
        theirs = client.get("/api/lola/memory", headers=token(REP2)).json()["memories"]
        assert [m["content"] for m in mine] == ["I like short answers"] and theirs == []
        mid = mine[0]["id"]
        assert client.delete(f"/api/lola/memory/{mid}", headers=token(REP2)).status_code == 404
        assert client.delete(f"/api/lola/memory/{mid}", headers=token(REP1)).status_code == 200

    def test_rep_cannot_write_company_memory(self, db, client):
        r = client.post("/api/lola/memory", json={"content": "x note", "scope": "company"}, headers=token(REP1))
        assert r.status_code == 403


class TestDelegationApi:
    def _post(self, client, uid, **body):
        payload = {"grantee_user_id": REP3, "resource_types": ["customer_account"], "days": 7, "reason": "vacation"}
        payload.update(body)
        return client.post("/api/lola/delegations", json=payload, headers=token(uid))

    def test_rep_can_delegate_own_accounts_with_expiry(self, db, client):
        r = self._post(client, REP2)
        assert r.status_code == 201 and r.json()["delegation"]["expires_at"]
        ask = client.post("/api/lola/ask", json={"message": "list my customers"}, headers=token(REP3)).json()
        names = {a["name"] for a in ask["tool_results"][0]["data"]["accounts"]}
        assert names == {"Bravo Imports", "Cedar Trading"}

    def test_cannot_delegate_what_you_do_not_have(self, db, client):
        assert self._post(client, REP2, resource_types=["receivables"]).status_code == 403

    def test_hr_and_payroll_are_not_delegable(self, db, client):
        assert self._post(client, OWNER, resource_types=["payroll"]).status_code == 403

    def test_cannot_delegate_hidden_accounts(self, db, client):
        assert self._post(client, REP2, account_ids=["QB-3"]).status_code == 403

    def test_delegated_rights_cannot_be_re_delegated(self, db, client):
        assert self._post(client, FINANCE, grantee_user_id=REP2, resource_types=["receivables"]).status_code == 201
        # REP2 now has receivables only by delegation; passing it on must fail.
        assert self._post(client, REP2, grantee_user_id=REP1, resource_types=["receivables"]).status_code == 403

    def test_expiry_is_mandatory_and_bounded(self, db, client):
        assert self._post(client, REP2, days=365).status_code == 422
        r = client.post("/api/lola/delegations", headers=token(REP2), json={
            "grantee_user_id": REP3, "resource_types": ["customer_account"], "reason": "x y"})
        assert r.status_code == 422

    def test_grantee_must_be_in_same_company(self, db, client):
        from lola_fakes import OWNER_B
        assert self._post(client, REP2, grantee_user_id=OWNER_B).status_code == 404

    def test_only_grantor_or_owner_can_revoke(self, db, client):
        did = self._post(client, REP2).json()["delegation"]["id"]
        assert client.delete(f"/api/lola/delegations/{did}", headers=token(REP1)).status_code == 404
        assert client.delete(f"/api/lola/delegations/{did}", headers=token(REP2)).status_code == 200
        ask = client.post("/api/lola/ask", json={"message": "list my customers"}, headers=token(REP3)).json()
        assert {a["name"] for a in ask["tool_results"][0]["data"]["accounts"]} == {"Cedar Trading"}


class TestActionsApi:
    def test_receipt_visible_to_requester_and_refreshed(self, db, client):
        r = client.post("/api/lola/ask", json={"message": "Draft an email to Acme Foods", "client_request_id": "x1"},
                        headers=token(REP1)).json()
        rid = r["receipts"][0]["receipt_id"]
        got = client.get(f"/api/lola/actions/{rid}", headers=token(REP1)).json()["receipt"]
        assert got["status"] == "pending_approval" and got["verified"] is False
        assert client.get(f"/api/lola/actions/{rid}", headers=token(REP2)).status_code == 404


class TestLegacyChatRepair:
    """The original /api/chat pipeline must not hand a rep the whole customer list."""

    def test_scoped_adapter_filters_customer_and_invoice_reads(self, db):
        import asyncio
        from app.lola.authorization import Authorizer, build_principal
        from app.lola.scoped_adapter import scope_adapter

        rep = scope_adapter(FakeAdapter(), Authorizer(build_principal(db, CO_A, REP1), db))
        owner = scope_adapter(base := FakeAdapter(), Authorizer(build_principal(db, CO_A, OWNER), db))
        assert owner is base
        names = [c.name for c in asyncio.run(rep.get_all_customers())]
        invs = {i.customer_id for i in asyncio.run(rep.get_orders_last_n_days(30))}
        assert names == ["Acme Foods", "Delta Supply"] and invs == {"QB-1"}

    def test_chat_message_uses_scoped_data_for_rep(self, db, client):
        from app.api.deps import get_adapter
        captured = {}

        async def fake_stream(message, history, data_summary, company_context, *a, **k):
            captured["summary"] = data_summary
            yield "ok"

        async def fake_intent(message, *a, **k):
            return "account_status"

        app.dependency_overrides[get_adapter] = lambda: FakeAdapter()
        with patch("app.api.chat.stream_chat", fake_stream), patch("app.api.chat.classify_intent", fake_intent):
            r = client.post("/api/chat/message", json={"message": "how are my accounts?"},
                            headers=token(REP1, "sales_rep"))
            r.read()
        assert r.status_code == 200
        # Only the rep's two accounts are counted (the company has four).
        assert "(2 total)" in captured["summary"]
        assert "Cedar" not in captured["summary"] and "Bravo" not in captured["summary"]
