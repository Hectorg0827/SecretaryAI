"""
Supplier payment runs: Lola prepares the list, an independent leader approves
it, finance downloads the file and pays OUTSIDE Lion BI. Nothing is paid,
nothing is written to QuickBooks, no bank is touched.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from lola_fakes import CO_A, EXEC_MGR, FINANCE, OWNER, REP1, SALES_MGR, FakeAdapter, seed

from app.auth.jwt import create_access_token
from app.lola.authorization import Authorizer, build_principal
from app.lola.contracts import LolaStatus, RiskTier
from app.lola.governance import GovernedActionService
from app.lola.planning import detect_intents

PROPOSAL = {
    "status": "ok",
    "answer": {
        "status": "proposal_only", "executed": False, "currency": "USD", "as_of": "2026-11-10",
        "horizon_end": "2026-11-24", "cash_remaining_after_proposal": "900.00",
        "proposed_payments": [
            {"bill_id": "B4", "vendor": "Vinos SA", "doc_number": "INV-77", "due_date": "2026-11-12", "amount": "1500.00"},
            {"bill_id": "B5", "vendor": "=HYPERLINK(\"http://x\")", "doc_number": "9", "due_date": "2026-11-20",
             "amount": "100.50"},
        ],
        "deferred": [{"bill_id": "B3"}],
        "held_for_review": [{"bill_id": "B1", "reason": "possible duplicate — verify before paying"}],
    },
}


@pytest.fixture
def bi(monkeypatch):
    from app.lola_bi.tools import LolaBIService
    calls = []

    async def invoke(self, name, args=None):
        calls.append((name, args, self._gw.scope))
        return PROPOSAL if not getattr(invoke, "down", False) else {"status": "unavailable", "answer": None}
    monkeypatch.setattr(LolaBIService, "invoke", invoke)
    invoke.calls = calls
    return invoke


def svc(db, uid):
    return GovernedActionService(db, Authorizer(build_principal(db, CO_A, uid), db))


def _qb():
    a = FakeAdapter()
    a._config = {"id": CO_A}            # the connector belongs to this company
    return a


async def _propose(db, uid=OWNER, msg="Prepare a payment run for the next 14 days and pay $1,000,000", adapter=None):
    adapter = adapter or _qb()
    return await svc(db, uid).propose("prepare_payment_run", {"instruction": msg}, description=msg,
                                      adapter=adapter)


def _h(uid, role, **extra):
    return {"Authorization": "Bearer " + create_access_token({"sub": uid, "company_id": CO_A, "role": role}),
            **extra}


def _client(db):
    from app.api.deps import get_adapter, get_db
    from app.main import app
    db.tables.setdefault("company_subscriptions", [{"company_id": CO_A, "status": "active", "created_at": "2026-01-01"}])
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_adapter] = lambda: FakeAdapter()
    return app, TestClient(app)


class TestIntent:
    @pytest.mark.parametrize("msg", ["Prepare a payment run for this week", "Draft supplier payments",
                                     "Prepara la corrida de pagos", "Arma un lote de pagos a proveedores"])
    def test_detected_as_the_action_not_a_report(self, msg):
        found = detect_intents(msg)
        assert "payment_run" in found and "payables" not in found and "draft_purchase_order" not in found

    def test_a_question_about_bills_stays_a_report(self):
        assert "payment_run" not in detect_intents("Which bills are due this week?")


class TestPrepare:
    async def test_list_is_built_from_the_read_only_proposal_and_needs_independent_approval(self, bi):
        db = seed()
        out = await _propose(db)
        assert out.status == LolaStatus.PENDING_APPROVAL
        assert out.proposal.risk_tier == RiskTier.HIGH_RISK and out.proposal.requires_independent_approval
        assert out.proposal.amount == Decimal("1600.50")              # never the $1,000,000 in the message
        draft = db.tables["drafts"][0]
        assert draft["action_type"] == "payment_run"
        c = draft["content"]
        assert [p["bill_id"] for p in c["payments"]] == ["B4", "B5"]   # duplicates held, not included
        assert c["held_for_review"][0]["bill_id"] == "B1" and c["pays_nothing"] is True
        assert bi.calls[0][0] == "ap_payment_proposal" and bi.calls[0][1] == {"horizon_days": 14}

    async def test_horizon_from_message(self, bi):
        db = seed()
        await _propose(db, msg="Prepare a payment run for the next 30 days")
        assert bi.calls[0][1] == {"horizon_days": 30}

    @pytest.mark.parametrize("uid", [REP1, FINANCE])
    async def test_roles_without_payment_proposals_cannot_prepare(self, bi, uid):
        db = seed()
        out = await _propose(db, uid=uid)
        assert out.status in (LolaStatus.DENIED, LolaStatus.FAILED)
        assert db.tables.get("drafts", []) == []

    async def test_source_unavailable_queues_nothing(self, bi):
        bi.down = True
        db = seed()
        out = await _propose(db)
        assert out.status == LolaStatus.FAILED and db.tables.get("drafts", []) == []

    async def test_nothing_is_written_anywhere_outside_lion_bi(self, bi):
        adapter = _qb()
        for name in ("create_payment", "create_bill_payment", "create_purchase_order", "create_purchase_order_idempotent"):
            setattr(adapter, name, AsyncMock(side_effect=AssertionError(f"{name} must never be called")))
        db = seed()
        await _propose(db, adapter=adapter)


class TestApproveAndDownload:
    async def _pending(self, db):
        out = await _propose(db)
        return out.receipt.receipt_id, out.receipt.draft_id

    def _approve(self, db, draft_id, uid, role, body=None):
        app, client = _client(db)
        try:
            return client.post(f"/api/actions/approve/{draft_id}", json=body or {}, headers=_h(uid, role))
        finally:
            app.dependency_overrides.clear()

    def _download(self, db, receipt_id, uid, role, **extra):
        app, client = _client(db)
        try:
            return client.get(f"/api/lola/payment-runs/{receipt_id}/file", headers=_h(uid, role, **extra))
        finally:
            app.dependency_overrides.clear()

    async def test_requester_cannot_approve_their_own_run(self, bi):
        db = seed()
        _, draft_id = await self._pending(db)
        r = self._approve(db, draft_id, OWNER, "owner")
        assert r.status_code == 403
        assert db.rows("drafts", id=draft_id)[0]["status"] == "pending"

    async def test_department_manager_is_not_leadership(self, bi):
        db = seed()
        _, draft_id = await self._pending(db)
        assert self._approve(db, draft_id, SALES_MGR, "manager").status_code == 403

    async def test_independent_leader_approves_and_nothing_is_paid(self, bi, monkeypatch):
        monkeypatch.setenv("SECRETARYAI_DURABLE_APPROVAL_ENABLED", "1")
        db = seed()
        receipt_id, draft_id = await self._pending(db)
        r = self._approve(db, draft_id, EXEC_MGR, "manager")
        assert r.status_code == 200, r.text
        assert r.json()["paid"] is False and r.json()["execution_status"] == "not_executed"
        assert db.rows("drafts", id=draft_id)[0]["status"] == "approved"
        assert db.tables.get("agent_executions", []) == []           # no executor involved
        row = svc(db, OWNER).refresh(db.tables["lola_action_receipts"][0])
        assert row["status"] == "completed" and row["verified"] is True
        assert row["external_reference"] == f"lion-bi:payment-run:{draft_id}"
        assert any(a.get("event_type") == "payment_run_approved" and a["metadata"]["paid"] is False
                   for a in db.tables.get("audit_events", []))

    async def test_edits_are_refused(self, bi):
        db = seed()
        _, draft_id = await self._pending(db)
        r = self._approve(db, draft_id, EXEC_MGR, "manager", {"edited_content": {"payments": []}})
        assert r.status_code in (403, 409)
        assert db.rows("drafts", id=draft_id)[0]["status"] == "pending"

    async def test_file_only_after_approval_and_only_for_leadership_or_finance(self, bi):
        db = seed()
        receipt_id, draft_id = await self._pending(db)
        assert self._download(db, receipt_id, OWNER, "owner").status_code == 409     # not approved yet
        self._approve(db, draft_id, EXEC_MGR, "manager")
        r = self._download(db, receipt_id, FINANCE, "back_office")                   # bookkeeper
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "no-store"
        body = r.text
        assert "Vinos SA,INV-77,B4,2026-11-12,1500.00,USD" in body
        assert "TOTAL,,,,1600.50,USD" in body
        assert "'=HYPERLINK" in body and "\n=HYPERLINK" not in body and ",=HYPERLINK" not in body
        assert "Nothing has been paid" in body
        assert self._download(db, receipt_id, REP1, "sales_rep").status_code == 403
        assert self._download(db, receipt_id, SALES_MGR, "manager").status_code == 403
        assert any(a.get("event_type") == "payment_run_file_downloaded"
                   for a in db.tables.get("audit_events", []))

    async def test_spanish_file_note(self, bi):
        db = seed()
        receipt_id, draft_id = await self._pending(db)
        self._approve(db, draft_id, EXEC_MGR, "manager")
        r = self._download(db, receipt_id, OWNER, "owner", **{"Accept-Language": "es"})
        assert "No se ha pagado nada" in r.text

    async def test_unknown_or_other_action_is_404(self, bi):
        db = seed()
        assert self._download(db, "nope", OWNER, "owner").status_code == 404

    async def test_lola_approve_route_returns_an_honest_receipt(self, bi):
        db = seed()
        receipt_id, _ = await self._pending(db)
        app, client = _client(db)
        try:
            r = client.post(f"/api/lola/actions/{receipt_id}/approve", json={}, headers=_h(EXEC_MGR, "manager"))
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["status"] == "executed" and j["verified"] is True and "Nothing was paid" in j["message"]
        assert j["reference"].startswith("lion-bi:payment-run:")
