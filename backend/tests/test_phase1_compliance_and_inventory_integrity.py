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
    def __init__(self, *, fail_table=None, write_mode="ok"):
        self.fail_table, self.write_mode = fail_table, write_mode
        self.rows = {}

    def table(self, name):
        return Query(self, name)


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
async def test_delete_must_report_missing_row_and_failed_database():
    for mode, expected in (("ok", 404), ("raise", 503), ("empty", 404)):
        db = FakeDB(write_mode=mode)
        with pytest.raises(HTTPException) as error:
            await api.delete_product("not-here", user=USER, db=db)
        assert error.value.status_code == expected


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
