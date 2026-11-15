"""Phase 1 safety gates: compliance integrity and Lola inventory parity.

Synthetic only: no provider calls, database writes, or deployment.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import compliance as api
from app.api.chat import _qty_to_status, _weeks_remaining
from app.api.inventory import _inventory_status
from app.ai.data_summarizer import summarize_inventory


USER = {"company_id": "tenant-a", "sub": "owner-a", "role": "owner"}

ENDPOINTS = [
    (api.add_product, api.AddProductRequest, {"sku": "ONE", "name": "Synthetic wine"},
     "compliance_products"),
    (api.add_license, api.AddStateLicenseRequest,
     {"state_code": "NY", "license_type": "importer", "license_number": "FAKE"}, "compliance_licenses"),
    (api.add_brand_registration, api.AddBrandRegistrationRequest,
     {"product_id": "synthetic-product", "state_code": "NY"}, "compliance_brand_registrations"),
    (api.add_federal_permit, api.AddFederalPermitRequest,
     {"permit_number": "FAKE"}, "compliance_federal_permits"),
    (api.add_distributor, api.AddDistributorRequest,
     {"state_code": "NY", "distributor_name": "Synthetic distribution"}, "compliance_distributors"),
    (api.add_cola, api.AddCOLARequest,
     {"product_id": "synthetic-product", "cola_number": "FAKE"}, "compliance_colas"),
]


@pytest.fixture(autouse=True)
def allow_compliance_record_policy(monkeypatch):
    """Existing persistence tests exercise an explicit policy allow decision."""
    from app.domain.policy import PolicyEngine

    async def allow(_self, _proposal):
        return SimpleNamespace(effect="allow", rule_id=None)
    monkeypatch.setattr(PolicyEngine, "evaluate", allow)


class AtomicRpc:
    def __init__(self, db, params):
        self.db = db
        self.params = params

    def execute(self):
        if self.db.write_mode == "raise":
            raise RuntimeError("synthetic atomic write failure")
        if self.db.write_mode == "empty":
            return SimpleNamespace(data={})
        if self.db.write_mode == "mismatch":
            return SimpleNamespace(data={
                "record": {"id": "wrong", "company_id": "wrong"},
                "audit_event_id": "synthetic-event",
            })
        if self.db.audit_failure:
            raise RuntimeError("synthetic atomic audit failure")
        table = self.params["p_table"]
        record = self.params["p_record"]
        others = [row for row in self.db.rows.get(table, []) if row.get("id") != record["id"]]
        others.append(dict(record))
        self.db.rows[table] = others
        self.db.rows.setdefault("audit_events", []).append({
            "id": "synthetic-event",
            "company_id": record["company_id"],
            "entity_type": table,
        })
        return SimpleNamespace(data={"record": dict(record), "audit_event_id": "synthetic-event"})


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.action, self.record, self.conditions = "select", None, []

    def select(self, *_args, **_kwargs):
        self.action = "select"
        return self

    def eq(self, key, value):
        self.conditions.append((key, value))
        return self

    def limit(self, _):
        return self

    def upsert(self, record):
        self.action, self.record = "upsert", record
        return self

    def insert(self, record):
        self.action, self.record = "insert", record
        return self

    def delete(self):
        self.action = "delete"
        return self

    def execute(self):
        if self.name == self.db.fail_table and self.action == "select":
            raise RuntimeError("simulated provider outage")
        rows = [
            dict(r) for r in self.db.rows.get(self.name, [])
            if all(r.get(k) == v for k, v in self.conditions)
        ]
        if self.action == "select":
            if self.name == "companies":
                rows = [{"id": "tenant-a", "name": "Synthetic Co",
                         "compliance_active_states": "NY"}]
            return SimpleNamespace(data=rows, count=len(rows))
        if self.action == "insert":
            if self.db.audit_failure:
                raise RuntimeError("synthetic audit unavailable")
            self.db.rows.setdefault(self.name, []).append(dict(self.record))
            return SimpleNamespace(data=[dict(self.record)])
        if self.action == "upsert":
            if self.db.write_mode == "raise":
                raise RuntimeError("synthetic database write outage")
            if self.db.write_mode == "empty":
                return SimpleNamespace(data=[])
            if self.db.write_mode == "mismatch":
                return SimpleNamespace(data=[{"id": "wrong", "company_id": "wrong"}])
            self.db.rows.setdefault(self.name, []).append(dict(self.record))
            return SimpleNamespace(data=[dict(self.record)])
        if self.action == "delete":
            if self.db.write_mode == "raise":
                raise RuntimeError("synthetic delete outage")
            if self.db.write_mode == "empty":
                return SimpleNamespace(data=[])
            self.db.rows[self.name] = [
                record for record in self.db.rows.get(self.name, [])
                if record not in rows
            ]
            return SimpleNamespace(data=rows)
        raise AssertionError(f"Unexpected operation {self.action}")


class FakeDB:
    def __init__(self, *, fail_table=None, write_mode="ok", audit_failure=False):
        self.fail_table, self.write_mode = fail_table, write_mode
        self.audit_failure = audit_failure
        self.rows = {}

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        assert name == "compliance_write_record_audited"
        assert params["p_policy_effect"] == "allow"
        return AtomicRpc(self, params)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,model,fields,table", ENDPOINTS)
async def test_every_compliance_create_requires_confirmed_persistence(handler, model, fields, table):
    for mode in ("raise", "empty", "mismatch"):
        db = FakeDB(write_mode=mode)
        with pytest.raises(HTTPException) as error:
            await handler(model(**fields), user=USER, db=db)
        assert error.value.status_code == 503
        assert db.rows.get(table, []) == []

    db = FakeDB()
    response = await handler(model(**fields), user=USER, db=db)
    assert response["id"] == db.rows[table][0]["id"]
    assert db.rows[table][0]["company_id"] == USER["company_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,model,fields,table", ENDPOINTS)
async def test_compliance_create_cannot_overwrite_foreign_tenant_row(handler, model, fields, table):
    db = FakeDB()
    db.rows[table] = [{"id": "shared-key", "company_id": "foreign-tenant"}]
    with pytest.raises(HTTPException) as error:
        await handler(model(id="shared-key", **fields), user=USER, db=db)
    assert error.value.status_code == 403
    assert db.rows[table] == [{"id": "shared-key", "company_id": "foreign-tenant"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", [
    "compliance_licenses", "compliance_products", "compliance_brand_registrations",
    "compliance_federal_permits", "compliance_distributors", "compliance_colas",
])
async def test_compliance_registry_never_fails_open_for_missing_table(table):
    db = FakeDB(fail_table=table)
    with pytest.raises(HTTPException) as error:
        await api.get_compliance_status(user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_unavailable_company_configuration_cannot_claim_compliance():
    db = FakeDB(fail_table="companies")
    with pytest.raises(HTTPException) as error:
        await api.get_compliance_status(user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_corrupted_registry_row_is_error_not_compliance_clearance():
    db = FakeDB()
    db.rows["compliance_licenses"] = [
        {"id": "bad", "company_id": USER["company_id"],
         "state_code": "NY", "license_type": "importer", "product_types": [],
         "status": "not-a-license-status"}
    ]
    with pytest.raises(HTTPException) as error:
        await api.get_compliance_status(user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_setup_status_does_not_treat_unreadable_license_table_as_zero():
    db = FakeDB(fail_table="compliance_licenses")
    with pytest.raises(HTTPException) as error:
        await api.get_setup_status(user=USER, db=db)
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,table", [
    (api.delete_product, "compliance_products"),
    (api.delete_license, "compliance_licenses"),
    (api.delete_brand_registration, "compliance_brand_registrations"),
    (api.delete_federal_permit, "compliance_federal_permits"),
    (api.delete_distributor, "compliance_distributors"),
    (api.delete_cola, "compliance_colas"),
])
async def test_compliance_hard_delete_forbidden_even_for_owner(handler, table):
    db = FakeDB()
    original = {"id": "existing", "company_id": USER["company_id"]}
    db.rows[table] = [original.copy()]
    with pytest.raises(HTTPException) as error:
        await handler("existing", user=USER, db=db)
    assert error.value.status_code == 403
    assert "retained" in error.value.detail.lower()
    assert db.rows[table] == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize("effect,status", [
    ("require_approval", 409), ("deny", 403),
])
async def test_policy_gate_blocks_compliance_mutations_with_durable_audit(monkeypatch, effect, status):
    from app.domain.policy import PolicyEngine

    async def decide(_self, _proposal):
        return SimpleNamespace(effect=effect, rule_id=None)
    monkeypatch.setattr(PolicyEngine, "evaluate", decide)
    db = FakeDB()
    with pytest.raises(HTTPException) as error:
        await api.add_product(
            api.AddProductRequest(sku="SYN", name="Synthetic wine"),
            user=USER, db=db,
        )
    assert error.value.status_code == status
    assert db.rows.get("compliance_products", []) == []
    audits = db.rows["audit_events"]
    assert len(audits) == 1
    assert audits[0]["policy_effect"] == effect
    assert audits[0]["company_id"] == USER["company_id"]


@pytest.mark.asyncio
async def test_failed_denial_audit_is_503_and_never_changes_record(monkeypatch):
    from app.domain.policy import PolicyEngine

    async def deny(_self, _proposal):
        return SimpleNamespace(effect="deny", rule_id=None)
    monkeypatch.setattr(PolicyEngine, "evaluate", deny)
    db = FakeDB(audit_failure=True)
    with pytest.raises(HTTPException) as error:
        await api.add_product(api.AddProductRequest(sku="SYN", name="Synthetic"),
                              user=USER, db=db)
    assert error.value.status_code == 503
    assert db.rows.get("compliance_products", []) == []


@pytest.mark.asyncio
async def test_unavailable_policy_fails_closed_with_audit(monkeypatch):
    from app.domain.policy import PolicyEngine

    async def unavailable(_self, _proposal):
        raise RuntimeError("Synthetic policy source failure")
    monkeypatch.setattr(PolicyEngine, "evaluate", unavailable)
    db = FakeDB()
    with pytest.raises(HTTPException) as error:
        await api.add_product(api.AddProductRequest(sku="SYN", name="Synthetic"),
                              user=USER, db=db)
    assert error.value.status_code == 503
    assert db.rows.get("compliance_products", []) == []
    assert db.rows["audit_events"][0]["policy_effect"] == "deny"


@pytest.mark.asyncio
async def test_atomic_audit_failure_rolls_back_record():
    db = FakeDB(audit_failure=True)
    with pytest.raises(HTTPException) as error:
        await api.add_product(api.AddProductRequest(sku="SYN", name="Synthetic"),
                              user=USER, db=db)
    assert error.value.status_code == 503
    assert db.rows.get("compliance_products", []) == []


@pytest.mark.asyncio
async def test_put_updates_with_record_and_audit_receipt():
    db = FakeDB()
    db.rows["compliance_products"] = [{
        "id": "synthetic-id", "company_id": USER["company_id"],
        "sku": "OLD", "name": "Original",
    }]
    result = await api.update_product(
        "synthetic-id",
        api.AddProductRequest(sku="NEW", name="Updated synthetic"),
        user=USER, db=db,
    )
    assert result["name"] == "Updated synthetic"
    assert len(db.rows["compliance_products"]) == 1
    assert db.rows["compliance_products"][0]["name"] == "Updated synthetic"
    assert db.rows["audit_events"][0]["entity_type"] == "compliance_products"


@pytest.mark.asyncio
async def test_compliance_delete_rejected_before_database_access():
    class NoDatabaseAccess:
        def table(self, _name):
            raise AssertionError("Rejected delete must not touch database")

    with pytest.raises(HTTPException) as error:
        await api.delete_product("foreign-or-missing", user=USER, db=NoDatabaseAccess())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_put_must_not_create_foreign_or_missing_record():
    db = FakeDB()
    with pytest.raises(HTTPException) as error:
        await api.update_product(
            "missing", api.AddProductRequest(sku="ONE", name="Synthetic"),
            user=USER, db=db,
        )
    assert error.value.status_code == 404
    assert db.rows == {}


@pytest.mark.parametrize("qty,rate,observed,expected", [
    (0, 0, False, "out_of_stock"),
    (50, 0, False, "unassessed"),
    (50, 0, True, "healthy"),
    (10, 10, True, "critical"),
    (50, 20, True, "low"),
    (80, 10, True, "healthy"),
])
def test_lola_inventory_matches_authoritative_screen(qty, rate, observed, expected):
    item = {
        "qb_id": "inventory-1", "product_name": "Synthetic product",
        "total_qty": qty, "weekly_sell_rate": rate,
        "weekly_sell_rate_observed": observed,
    }
    assert _qty_to_status(item) == expected
    assert _qty_to_status(item) == _inventory_status(item).status
    assert _weeks_remaining(item) == (
        float(_inventory_status(item).weeks_remaining)
        if _inventory_status(item).weeks_remaining is not None else None
    )


def test_unassessed_product_is_explicit_in_lola_summary():
    summary = summarize_inventory([
        {"product_name": "Synthetic", "total_qty": 50, "stock_status": "unassessed",
         "weeks_remaining": None}
    ])
    assert "[UNASSESSED]" in summary
    assert "could not be assessed" in summary
    assert "0 products at verified healthy stock levels" in summary
