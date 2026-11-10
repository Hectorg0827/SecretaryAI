"""
WS3 receiving → protected two-person stock path.

Off by default (postings stay review-only drafts). When switched on and one
open PO matches, the count is staged with stage_warehouse_receipt; stock is
only booked when a different owner/manager approves it (enforced in SQL).
"""
import pytest

from .test_logistics_api import _seed_shipment, _tok, client, db  # noqa: F401  (fixtures)

COUNT = {"shipment_id": "SHP-1",
         "lines": [{"sku": "WINE-RED", "good_cases": 1180, "damaged_cases": 20, "lot": "L2026A"}],
         "evidence": {"damage_noted_on_delivery_receipt": True, "damage_cause": "transit_handling",
                      "delivering_carrier": "Acme Drayage"}}


class _Rpc:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        self.db.rpc_calls.append((self.name, self.params))
        if self.db.rpc_error is not None:
            raise self.db.rpc_error
        return type("R", (), {"data": {"staged_receipt_id": "stg-1", "status": "pending",
                                       "replayed": len(self.db.rpc_calls) > 1, "inventory_posted": False}})()


class DbError(Exception):
    def __init__(self, code):
        super().__init__("db")
        self.code = code


@pytest.fixture
def stockdb(db, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SECRETARYAI_LOLA_STOCK_STAGING_ENABLED", "1")
    db.rpc_calls, db.rpc_error = [], None
    db.rpc = lambda name, params: _Rpc(db, name, params)
    db.tables["purchasing_orders"] = [{"id": "po-1", "company_id": "company-A", "po_number": "PO-1001",
                                       "status": "approved"}]
    db.tables["purchasing_order_lines"] = [{"company_id": "company-A", "po_id": "po-1", "sku": "WINE-RED",
                                            "qty_ordered": 1200, "qty_received": 0}]
    return db


def _post(client, role="owner", sub="mgr-1"):  # noqa: F811
    return client.post("/api/logistics/receiving/reconcile", headers=_tok(role=role, sub=sub), json=COUNT)


class TestOffByDefault:
    def test_postings_stay_review_only(self, client, db, monkeypatch):  # noqa: F811
        monkeypatch.delenv("SECRETARYAI_LOLA_STOCK_STAGING_ENABLED", raising=False)
        _seed_shipment(db)
        j = _post(client).json()
        assert j["inventory_staging"] == {"staged": False, "reason": "off",
                                          "message": j["inventory_staging"]["message"]}
        assert len(db.tables["drafts"]) == 3
        assert {a["status"] for a in j["approvals"]} == {"pending_approval"}


class TestStaged:
    def test_count_is_staged_not_posted(self, client, stockdb):  # noqa: F811
        _seed_shipment(stockdb)
        r = _post(client)
        assert r.status_code == 200, r.text
        j = r.json()
        st = j["inventory_staging"]
        assert st["staged"] is True and st["inventory_posted"] is False
        assert st["approve_path"] == "/api/logistics/receipts/stg-1/approve"
        (name, params), = stockdb.rpc_calls
        assert name == "stage_warehouse_receipt"                      # never the posting functions
        assert params["p_po_number"] == "PO-1001" and params["p_actor"] == "mgr-1"
        assert params["p_lines"] == [{"sku": "WINE-RED", "lot_code": "L2026A", "accepted_qty": 1180,
                                      "damaged_qty": 20, "quarantined_qty": 0}]
        assert params["p_receipt_key"].startswith("lola-rcv:")
        # Stock postings are NOT duplicated as dead-end drafts; the claim still is.
        assert [d["action_type"] for d in stockdb.tables["drafts"]] == ["logistics.file_claim"]
        assert "pending_receiving_approval" in {a["status"] for a in j["approvals"]}
        assert not stockdb.tables.get("stock_movements") and not stockdb.tables.get("stock_balances")

    def test_same_count_twice_uses_the_same_receipt_key(self, client, stockdb):  # noqa: F811
        _seed_shipment(stockdb)
        _post(client)
        _post(client)
        keys = {p["p_receipt_key"] for _, p in stockdb.rpc_calls}
        assert len(stockdb.rpc_calls) == 2 and len(keys) == 1

    def test_sku_is_matched_case_insensitively_to_the_po(self, client, stockdb):  # noqa: F811
        stockdb.tables["purchasing_order_lines"][0]["sku"] = "wine-red"
        _seed_shipment(stockdb)
        _post(client)
        assert stockdb.rpc_calls[0][1]["p_lines"][0]["sku"] == "wine-red"


class TestNotStaged:
    @pytest.mark.parametrize("change,reason", [
        (lambda d: d.tables.__setitem__("purchasing_orders", []), "no_open_po"),
        (lambda d: d.tables["purchasing_orders"][0].__setitem__("status", "draft"), "no_open_po"),
        (lambda d: d.tables["purchasing_orders"][0].__setitem__("status", "received"), "no_open_po"),
        (lambda d: d.tables["purchasing_order_lines"][0].__setitem__("sku", "WINE-WHITE"), "sku_not_on_po"),
        (lambda d: d.tables["purchasing_order_lines"][0].__setitem__("qty_received", 1150), "over_receipt"),
        (lambda d: d.tables["purchasing_orders"].append(
            {"id": "po-2", "company_id": "company-A", "po_number": "PO-1001", "status": "approved"}),
         "ambiguous_po"),
        (lambda d: d.tables["purchasing_orders"][0].__setitem__("company_id", "company-B"), "no_open_po"),
    ])
    def test_falls_back_to_review_only_drafts(self, client, stockdb, change, reason):  # noqa: F811
        _seed_shipment(stockdb)
        change(stockdb)
        j = _post(client).json()
        assert j["inventory_staging"]["staged"] is False and j["inventory_staging"]["reason"] == reason
        assert stockdb.rpc_calls == []
        assert len(stockdb.tables["drafts"]) == 3

    @pytest.mark.parametrize("code,reason", [("42501", "not_permitted"), ("23505", "refused"),
                                             ("", "unavailable")])
    def test_database_refusal_falls_back(self, client, stockdb, code, reason):  # noqa: F811
        stockdb.rpc_error = DbError(code)
        _seed_shipment(stockdb)
        j = _post(client).json()
        assert j["inventory_staging"] == {"staged": False, "reason": reason,
                                          "message": j["inventory_staging"]["message"]}
        assert len(stockdb.tables["drafts"]) == 3

    def test_read_failure_never_stages(self, client, stockdb):  # noqa: F811
        _seed_shipment(stockdb)
        stockdb.tables["purchasing_orders"] = None                    # breaks the read
        j = _post(client).json()
        assert j["inventory_staging"]["reason"] == "unavailable" and stockdb.rpc_calls == []


class TestTenantPolicyComesFirst:
    """A tenant rule that denies stock postings stops staging; the receiving approval path can't see policy."""

    def _policy(self, monkeypatch, effect=None, error=False):
        from types import SimpleNamespace

        from app.domain.policy import PolicyEngine

        async def evaluate(self, proposal):
            if error:
                raise RuntimeError("policy store down")
            stock = proposal.action_type.startswith("logistics.post_inventory")
            return SimpleNamespace(effect=effect if stock else "require_approval", rule_name="r",
                                   approval_roles=["owner"])
        monkeypatch.setattr(PolicyEngine, "evaluate", evaluate)

    def test_deny_rule_blocks_staging(self, client, stockdb, monkeypatch):  # noqa: F811
        self._policy(monkeypatch, effect="deny")
        _seed_shipment(stockdb)
        j = _post(client).json()
        assert j["inventory_staging"]["staged"] is False
        assert j["inventory_staging"]["reason"] == "blocked_by_policy"
        assert stockdb.rpc_calls == []
        assert {a["status"] for a in j["approvals"] if a["action_type"].startswith("logistics.post_inventory")} \
            == {"blocked_by_policy"}

    def test_policy_unavailable_blocks_staging(self, client, stockdb, monkeypatch):  # noqa: F811
        self._policy(monkeypatch, error=True)
        _seed_shipment(stockdb)
        j = _post(client).json()
        assert j["inventory_staging"]["reason"] == "policy_unavailable" and stockdb.rpc_calls == []

    @pytest.mark.parametrize("effect", ["allow", "require_approval"])
    def test_non_deny_policy_still_stages(self, client, stockdb, monkeypatch, effect):  # noqa: F811
        self._policy(monkeypatch, effect=effect)
        _seed_shipment(stockdb)
        j = _post(client).json()
        assert j["inventory_staging"]["staged"] is True and len(stockdb.rpc_calls) == 1
