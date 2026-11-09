"""
HTTP tests for /api/logistics — the journey endpoints and regression tests
for previously broken pipeline endpoints.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import copy

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.jwt import create_access_token
from app.intelligence.wholesale_distribution.logistics.journey import LogisticsEvent
from app.main import app

from .journey_helpers import BOL, CONTAINER


class FakeSupabase:
    """Minimal in-memory Supabase: tables are lists of dicts; filters honoured."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {"companies": [], "logistics_events": [], "drafts": [],
                                              "action_log": [], "procurement_cycles": [],
                                              "policy_rules": [], "company_features": []}
        self.fail_writes_to: set[str] = set()

    def table(self, name):
        return _Query(self, name)


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.row = db, name, [], "select", None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def maybe_single(self): return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def insert(self, row):
        self.op, self.row = "insert", row
        return self

    def upsert(self, row, on_conflict="", **k):
        self.op, self.row, self.conflict = "upsert", row, on_conflict
        return self

    def update(self, row):
        self.op, self.row = "update", row
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op in ("insert", "upsert"):
            if self.name in self.db.fail_writes_to:
                raise ConnectionError("db down")
            if self.op == "upsert" and self.conflict:
                keys = self.conflict.split(",")
                if any(all(r.get(k) == self.row.get(k) for k in keys) for r in rows):
                    return type("R", (), {"data": []})()
            rows.append(copy.deepcopy(self.row))
            return type("R", (), {"data": [self.row]})()
        data = [r for r in rows if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        return type("R", (), {"data": data})()


def _tok(role="owner", company="company-A", sub=None, **extra):
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub or f"{role}-user", "company_id": company, "role": role, **extra})}


@pytest.fixture
def db():
    d = FakeSupabase()
    app.dependency_overrides[get_db] = lambda: d
    yield d
    app.dependency_overrides.clear()


@pytest.fixture
def client(db):
    return TestClient(app)


def _seed_shipment(db, company="company-A"):
    """Integration (connector) path: authoritative events written server-side."""
    from app.api.logistics import ingest_integration_events
    evs = [
        LogisticsEvent(company_id=company, shipment_id="SHP-1", kind="shipment_created", source="erp",
                       payload={"po_numbers": ["PO-1001"], "supplier_id": "SUP-1", "supplier_name": "Vinos SA",
                                "container_numbers": [CONTAINER], "bol_number": BOL, "mode": "ocean",
                                "supply_chain_type": "overseas", "carrier": "Maersk",
                                "lines": [{"sku": "WINE-RED", "qty_cases": 1200, "unit_cost": 24.0}],
                                "commercial": {"payment_terms": "Net 60"}}),
        LogisticsEvent(company_id=company, shipment_id="SHP-1", kind="milestone", code="vessel_departed",
                       source="carrier_api", occurred_at="2026-09-21"),
        LogisticsEvent(company_id=company, shipment_id="SHP-1", kind="eta", source="carrier_api",
                       payload={"eta": "2026-11-10"}),
        LogisticsEvent(company_id=company, shipment_id="SHP-1", kind="allocation", source="erp",
                       payload={"order_id": "SO-1", "customer_id": "C1", "customer_name": "Bodega Uno",
                                "sales_rep_id": "rep-1", "sku": "WINE-RED", "qty_cases": 600}),
    ]
    return ingest_integration_events(db, company, evs)


# ── Journey endpoints ────────────────────────────────────────────────────────


class TestJourneyAPI:
    def test_people_cannot_claim_machine_sources(self, client, db):
        _seed_shipment(db)
        for src in ("carrier_api", "customs_system", "broker_edi", "wms", "erp", "document_validated"):
            r = client.post("/api/logistics/journey/events", headers=_tok(), json={
                "shipment_id": "SHP-1", "kind": "milestone", "code": "customs_released", "source": src})
            assert r.status_code == 403, (src, r.text)

    def test_user_confirmed_recorded_under_callers_id(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/journey/events", headers=_tok(sub="ops-9", role="back_office"), json={
            "shipment_id": "SHP-1", "kind": "milestone", "code": "vessel_arrived",
            "source": "user_confirmed", "occurred_at": "2026-11-10",
            "payload": {"confirmed_by": "someone-else"}})
        assert r.status_code == 200 and r.json()["verified"]
        row = db.tables["logistics_events"][-1]
        assert row["confirmed_by"] == "ops-9"

    def test_email_report_then_human_confirmation(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/journey/events", headers=_tok(), json={
            "shipment_id": "SHP-1", "kind": "milestone", "code": "customs_released",
            "source": "email_parsed", "source_ref": "gmail-1"})
        evid = r.json()["evidence_id"]
        assert evid and not r.json()["verified"]
        d = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok()).json()
        assert "customs_released" not in d["milestones"]
        r2 = client.post(f"/api/logistics/journey/evidence/{evid}/confirm", headers=_tok(sub="mgr-1"), json={})
        assert r2.status_code == 200 and r2.json()["verified"]
        d = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok()).json()
        assert d["milestones"]["customs_released"]["confirmed_by"] == "mgr-1"

    def test_sales_rep_cannot_record_or_confirm(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/journey/events", headers=_tok(role="sales_rep", sub="rep-1"), json={
            "shipment_id": "SHP-1", "kind": "milestone", "code": "vessel_arrived", "source": "user_confirmed"})
        assert r.status_code == 403

    def test_viewer_has_no_logistics_access(self, client, db):
        _seed_shipment(db)
        assert client.get("/api/logistics/journey/shipments", headers=_tok(role="viewer")).status_code == 403

    def test_sales_rep_projection_over_http(self, client, db):
        _seed_shipment(db)
        r1 = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok(role="sales_rep", sub="rep-1"))
        assert r1.status_code == 200
        body = r1.text
        assert "Net 60" not in body and "unit_cost" not in body and "Vinos" not in body
        r2 = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok(role="sales_rep", sub="rep-2"))
        assert r2.status_code == 404

    def test_cross_company_isolation(self, client, db):
        _seed_shipment(db, company="company-A")
        r = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok(company="company-B"))
        assert r.status_code == 404
        assert client.get("/api/logistics/journey/shipments", headers=_tok(company="company-B")).json()["count"] == 0
        r = client.post("/api/logistics/ask", headers=_tok(company="company-B"),
                        json={"question": f"Where is container {CONTAINER}?"})
        assert "couldn't find" in r.json()["answer"]

    def test_duplicate_event_over_http(self, client, db):
        _seed_shipment(db)
        body = {"shipment_id": "SHP-1", "kind": "milestone", "code": "vessel_arrived",
                "source": "user_confirmed", "occurred_at": "2026-11-10", "source_ref": "call-1"}
        h = _tok(sub="ops-1")
        assert client.post("/api/logistics/journey/events", headers=h, json=body).json()["applied"]
        assert client.post("/api/logistics/journey/events", headers=h, json=body).json()["duplicate"]
        assert sum(1 for r in db.tables["logistics_events"] if r["code"] == "vessel_arrived") == 1

    def test_store_outage_returns_503_and_changes_nothing(self, client, db):
        _seed_shipment(db)
        n = len(db.tables["logistics_events"])
        db.fail_writes_to.add("logistics_events")
        r = client.post("/api/logistics/journey/events", headers=_tok(), json={
            "shipment_id": "SHP-1", "kind": "milestone", "code": "vessel_arrived", "source": "user_confirmed"})
        assert r.status_code == 503 and "nothing was changed" in r.json()["detail"]
        assert len(db.tables["logistics_events"]) == n

    def test_document_ingest_links_and_stays_unverified(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/documents/ingest", headers=_tok(), json={
            "text": f"Customs broker notice — container {CONTAINER}: cargo released by CBP."})
        assert r.status_code == 200
        j = r.json()
        assert j["linked"] and j["document"]["fields"]["customs_status"] == "released"
        assert j["ledger_results"][0]["evidence_id"]
        d = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok()).json()
        assert "customs_released" not in d["milestones"]

    def test_ask_endpoint(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/ask", headers=_tok(),
                        json={"question": f"Where is container {CONTAINER}?", "as_of": "2026-11-09"})
        assert r.status_code == 200 and "verified ETA 2026-11-10" in r.json()["answer"]

    def test_monitor_endpoint(self, client, db):
        _seed_shipment(db)
        r = client.get("/api/logistics/monitor?as_of=2026-11-09", headers=_tok())
        assert r.status_code == 200
        assert "MISSING_BOL" in {a["code"] for a in r.json()["alerts"]}

    def test_landed_cost_requires_financials(self, client, db):
        _seed_shipment(db)
        assert client.get("/api/logistics/journey/shipments/SHP-1/landed-cost",
                          headers=_tok(role="back_office")).status_code == 403
        r = client.get("/api/logistics/journey/shipments/SHP-1/landed-cost", headers=_tok())
        assert r.status_code == 200 and r.json()["landed_cost_per_case_usd"] == 24.0


class TestReceivingAcceptanceOverHTTP:
    def test_1200_expected_1180_good_20_damaged(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/receiving/reconcile", headers=_tok(sub="mgr-1"), json={
            "shipment_id": "SHP-1",
            "lines": [{"sku": "WINE-RED", "good_cases": 1180, "damaged_cases": 20, "lot": "L2026A"}],
            "evidence": {"damage_noted_on_delivery_receipt": True, "damage_cause": "transit_handling",
                         "delivering_carrier": "Acme Drayage"},
        })
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["totals"]["good_cases"] == 1180 and j["totals"]["damaged_cases"] == 20
        assert j["inventory_segregation"]["QUARANTINE-DAMAGED"] == 20
        assert j["financial_implications"]["damaged_value_at_po_cost_usd"] == 480.0
        assert j["claims"][0]["claim_type"] == "carrier_cargo_claim"
        assert {a["status"] for a in j["approvals"]} == {"pending_approval"}
        # Only the event log and the approval queue were written — no stock, no money.
        written = {t for t, rows in db.tables.items() if rows} - {"logistics_events", "drafts", "action_log"}
        assert written == set(), written
        assert len(db.tables["drafts"]) == 3
        assert all(d["status"] == "pending" for d in db.tables["drafts"])
        # Ledger now shows the verified receipt + a damage exception
        d = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok()).json()
        assert d["milestones"]["warehouse_received"]["confirmed_by"] == "mgr-1"
        assert "RECEIPT_DAMAGE" in {x["code"] for x in d["exceptions"]}
        assert "SEQUENCE_ANOMALY" in {x["code"] for x in d["exceptions"]}   # no customs release on file

    def test_logistics_staff_see_quantities_not_money(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/receiving/reconcile", headers=_tok(role="back_office", sub="wh-1"), json={
            "shipment_id": "SHP-1", "lines": [{"sku": "WINE-RED", "good_cases": 1180, "damaged_cases": 20}]})
        j = r.json()
        assert r.status_code == 200
        assert "financial_implications" not in j
        assert j["claims"][0]["claimed_amount_estimate_usd"] is None
        assert "$" not in j["claims"][0]["narrative"]

    def test_sales_rep_cannot_reconcile(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/receiving/reconcile", headers=_tok(role="sales_rep", sub="rep-1"),
                        json={"shipment_id": "SHP-1", "lines": [{"sku": "WINE-RED", "good_cases": 1}]})
        assert r.status_code == 403

    def test_negative_quantity_rejected(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/receiving/reconcile", headers=_tok(),
                        json={"shipment_id": "SHP-1", "lines": [{"sku": "WINE-RED", "good_cases": -1}]})
        assert r.status_code == 422


# ── Regression tests for previously broken pipeline endpoints ────────────────


class TestLegacyEndpointRegressions:
    def test_reorder_evaluate_uses_sales_history(self, client, db):
        r = client.post("/api/logistics/reorder/evaluate", headers=_tok(), json={"skus": [{
            "sku": "WINE-RED", "name": "Red", "supplier_id": "SUP-1", "stock_on_hand": 50,
            "daily_sales": [20.0] * 90, "lead_time_days": [60, 65, 70]}]})
        assert r.status_code == 200, r.text
        q = r.json()["reorder_queue"]
        assert q and q[0]["sku"] == "WINE-RED" and q[0]["urgency"] == "red"

    def test_vendor_parse_response(self, client, db):
        r = client.post("/api/logistics/vendor/parse-response", headers=_tok(), json={
            "po_number": "PO-1001", "supplier_id": "SUP-1", "email_subject": "PO-1001 confirmed",
            "email_body": "We confirm PO-1001. Ship date: 2026-09-20."})
        assert r.status_code == 200, r.text
        assert r.json()["verification"].startswith("unverified")

    def test_freight_invoice(self, client, db):
        r = client.post("/api/logistics/freight/parse-invoice", headers=_tok(), json={
            "booking_id": "BK-1", "invoice_text": "Ocean freight $2,800.00\nDrayage $650.00", "total_cases": 1200})
        assert r.status_code == 200, r.text
        assert r.json()["total_cost"] == 3450.0

    def test_receipt_process(self, client, db):
        r = client.post("/api/logistics/receipt/process", headers=_tok(), json={
            "booking_id": "BK-1", "po_number": "PO-1",
            "line_items": [{"sku": "A", "qty_ordered": 10, "qty_received": 8}]})
        assert r.status_code == 200, r.text
        assert r.json()["shortage_count"] == 1

    def test_seasonal_indices(self, client, db):
        r = client.post("/api/logistics/learning/seasonal-indices", headers=_tok(), json={
            "sku": "A", "daily_sales": [{"date": f"2026-01-{d:02d}", "units": 5} for d in range(1, 29)]})
        assert r.status_code == 200, r.text

    def test_broker_email_not_released_is_not_release(self, client, db):
        r = client.post("/api/logistics/customs/parse-broker-email", headers=_tok(), json={
            "booking_id": "BK-1", "subject": "Container status",
            "body": "Your cargo has NOT been released. CBP placed a hold for documentation review."})
        assert r.status_code == 200
        assert r.json()["milestone_detected"] == "hold_placed"
        assert r.json()["verification"].startswith("unverified")

    def test_broker_email_evidence_recorded_on_shipment(self, client, db):
        _seed_shipment(db)
        r = client.post("/api/logistics/customs/parse-broker-email", headers=_tok(), json={
            "booking_id": "BK-1", "subject": "Released", "body": "Cargo released by CBP.",
            "shipment_id": "SHP-1", "message_id": "gmail-42"})
        assert r.json()["ledger_evidence"]["evidence_id"]
        d = client.get("/api/logistics/journey/shipments/SHP-1", headers=_tok()).json()
        assert "customs_released" not in d["milestones"]
        assert d["pending_evidence"][0]["source_ref"] == "gmail-42"
