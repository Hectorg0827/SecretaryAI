"""Synthetic-only internal price preparation APIs: no financial or agency writes."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

from app.api.insights import price_preparations as api

TENANT = "00000000-0000-4000-8000-000000000011"
FOREIGN = "00000000-0000-4000-8000-000000000099"
ACTOR = "00000000-0000-4000-8000-000000000012"
RULE_ID = "00000000-0000-4000-8000-000000000013"
BOOK_ID = "00000000-0000-4000-8000-000000000014"
CASE_ID = "00000000-0000-4000-8000-000000000015"
USER = {"company_id": TENANT, "sub": ACTOR, "role": "owner"}
RULE = {"id": RULE_ID, "version": 1, "program_key": "NJ_ECPL",
        "country_code": "US", "region_code": "NJ"}


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.inserting = None
        self.limit_n = None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, str(value)))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def range(self, *_args):
        return self

    def insert(self, item):
        self.inserting = item
        return self

    def execute(self):
        if self.table == self.db.fail_source:
            raise RuntimeError("Synthetic outage")
        if self.inserting is not None:
            if self.db.audit_unavailable:
                raise RuntimeError("Synthetic audit outage")
            self.db.audits.append(self.inserting)
            return SimpleNamespace(data=[dict(self.inserting)])
        rows = self.db.tables.get(self.table, [])
        return SimpleNamespace(data=[
            dict(r) for r in rows if all(str(r.get(k)) == v for k, v in self.filters)
        ][:self.limit_n])


class Rpc:
    def __init__(self, db, name, args):
        self.db, self.name, self.args = db, name, args

    def execute(self):
        if self.db.rpc_unavailable:
            raise RuntimeError("Synthetic atomic transaction failure")
        if self.db.unconfirmed_receipt:
            return SimpleNamespace(data={"source_count": 1})
        if self.name == "create_price_posting_preparation":
            return SimpleNamespace(data={
                "preparation_id": CASE_ID,
                "audit_event_id": "audit-synthetic",
                "source_fingerprint": "0"*32,
                "source_count": len(self.args["p_book_ids"]),
                "state": "internal_preparation_only",
                "submission_permitted": False,
                "agency_confirmation": False,
                "price_publication_permitted": False,
            })
        if self.name == "record_price_posting_external_reference":
            return SimpleNamespace(data={
                "reference_id": "synthetic-ref-id",
                "audit_event_id": "audit-synthetic",
                "provenance_status": "human_reported_unverified",
                "agency_confirmation": False,
                "submission_permitted": False,
            })
        raise AssertionError("Unexpected mutation " + self.name)


class FakeDB:
    def __init__(self):
        self.calls = []
        self.audits = []
        self.fail_source = None
        self.audit_unavailable = False
        self.rpc_unavailable = False
        self.unconfirmed_receipt = False
        self.tables = {
            "compliance_price_posting_preparations": [{
                "id": CASE_ID, "company_id": TENANT,
                "rule_id": RULE_ID, "rule_version": 1,
                "created_by": ACTOR, "effective_date": "2026-12-01",
                "price_book_snapshot": [], "source_fingerprint": "0"*32,
                "state": "internal_preparation_only",
                "created_at": "2026-11-15T00:00:00Z",
            }],
            "compliance_price_posting_external_references": [{
                "id": "synthetic-ref", "company_id": TENANT,
                "preparation_id": CASE_ID, "created_by": ACTOR,
                "reference_kind": "agency_message_reference",
                "external_reference": "synthetic-regulator-document",
                "claimed_event_date": None, "note": "Human reported source only.",
                "provenance_status": "human_reported_unverified",
                "created_at": "2026-11-15T00:00:00Z",
            }],
        }

    def table(self, name):
        self.calls.append(name)
        if name not in self.tables and name != "audit_events":
            raise AssertionError("Unexpected table " + name)
        return Query(self, name)

    def rpc(self, name, args):
        self.calls.append("rpc:" + name)
        self.rpc_args = args
        return Rpc(self, name, args)


@pytest.fixture
def policy(monkeypatch):
    state = {"effect": "allow", "failure": False}

    async def evaluate(_self, proposal):
        assert proposal.company_id == TENANT
        assert proposal.requested_by == ACTOR
        assert proposal.capability == "compliance.price_preparation"
        assert proposal.action_class.value == "commit"
        if state["failure"]:
            raise RuntimeError("Synthetic policy outage")
        return SimpleNamespace(effect=state["effect"], rule_id=None)

    monkeypatch.setattr(api.PolicyEngine, "evaluate", evaluate)
    monkeypatch.setattr(api, "_latest_rule", lambda _db, _program: dict(RULE))
    monkeypatch.setattr(api, "_human_reviewed", lambda _db, _company, _rule: True)
    return state


def prep(**changes):
    value = {
        "program_key": "NJ_ECPL",
        "rule_id": UUID(RULE_ID),
        "version": 1,
        "effective_date": date(2026, 12, 1),
        "price_book_ids": [UUID(BOOK_ID)],
        "acknowledged_no_filing_authority": True,
    }
    value.update(changes)
    return api.PrepareInternalCaseRequest(**value)


def external(**changes):
    value = {
        "kind": "agency_message_reference",
        "external_reference": "synthetic-external-document",
        "claimed_event_date": None,
        "note": "Human noted an external document; this is not agency verified.",
        "acknowledged_unverified": True,
    }
    value.update(changes)
    return api.AddHumanExternalReferenceRequest(**value)


@pytest.mark.asyncio
async def test_create_packet_freezes_selected_ids_only_and_never_files(policy):
    db = FakeDB()
    result = await api.create_internal_preparation(prep(), user=USER, db=db)
    assert result["state"] == "internal_preparation_only"
    assert result["submission_permitted"] is False
    assert result["agency_confirmation"] is False
    assert db.rpc_args["p_company_id"] == TENANT
    assert db.rpc_args["p_actor_id"] == ACTOR
    assert db.rpc_args["p_book_ids"] == [BOOK_ID]
    assert "price" not in db.rpc_args
    assert not db.audits


@pytest.mark.asyncio
@pytest.mark.parametrize("role",["pricing_billbacks","sales_rep","viewer"])
async def test_nonmanagers_cannot_create_cases(policy,role):
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(prep(),user=dict(USER,role=role),db=db)
    assert err.value.status_code == 403
    assert not db.calls


@pytest.mark.asyncio
async def test_no_filing_acknowledgement_required(policy):
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(
            prep(acknowledged_no_filing_authority=False),user=USER,db=db)
    assert err.value.status_code == 422
    assert not db.calls


@pytest.mark.asyncio
async def test_duplicate_book_ids_rejected(policy):
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(
            prep(price_book_ids=[UUID(BOOK_ID),UUID(BOOK_ID)]),user=USER,db=db)
    assert err.value.status_code == 422
    assert not db.calls


@pytest.mark.asyncio
async def test_unreviewed_source_cannot_prepare_case(policy,monkeypatch):
    monkeypatch.setattr(api,"_human_reviewed",lambda *_:False)
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(prep(),user=USER,db=db)
    assert err.value.status_code == 409
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect,status",[("deny",403),("require_approval",409)])
async def test_policy_denial_is_audited_not_written(policy,effect,status):
    policy["effect"]=effect
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(prep(),user=USER,db=db)
    assert err.value.status_code == status
    assert len(db.audits)==1
    assert db.audits[0]["policy_effect"]==effect
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
async def test_policy_source_outage_fail_closed_with_audit(policy):
    policy["failure"]=True
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(prep(),user=USER,db=db)
    assert err.value.status_code==503
    assert len(db.audits)==1
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type",["rpc_unavailable","unconfirmed_receipt"])
async def test_unconfirmed_packet_audit_cannot_report_success(policy,error_type):
    db=FakeDB()
    setattr(db,error_type,True)
    with pytest.raises(HTTPException) as err:
        await api.create_internal_preparation(prep(),user=USER,db=db)
    assert err.value.status_code==503


@pytest.mark.asyncio
async def test_reference_is_human_reported_not_agency_receipt(policy):
    db=FakeDB()
    result=await api.add_human_external_reference(
        UUID(CASE_ID),external(),user=USER,db=db)
    assert result["provenance_status"]=="human_reported_unverified"
    assert result["agency_confirmation"] is False
    assert result["submission_permitted"] is False
    assert db.rpc_args["p_preparation_id"]==CASE_ID
    assert db.rpc_args["p_actor_id"]==ACTOR


@pytest.mark.asyncio
async def test_foreign_preparation_is_404_before_reference_rpc(policy):
    db=FakeDB()
    db.tables["compliance_price_posting_preparations"][0]["company_id"]=FOREIGN
    with pytest.raises(HTTPException) as err:
        await api.add_human_external_reference(
            UUID(CASE_ID),external(),user=USER,db=db)
    assert err.value.status_code==404
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
async def test_cannot_add_unacknowledged_external_reference(policy):
    db=FakeDB()
    with pytest.raises(HTTPException) as err:
        await api.add_human_external_reference(
            UUID(CASE_ID),external(acknowledged_unverified=False),user=USER,db=db)
    assert err.value.status_code==422
    assert not db.calls


@pytest.mark.asyncio
async def test_case_list_fails_closed_on_source_outage(policy):
    db=FakeDB()
    db.fail_source="compliance_price_posting_preparations"
    with pytest.raises(HTTPException) as err:
        await api.list_internal_preparations(Response(),offset=0,limit=50,user=USER,db=db)
    assert err.value.status_code==503


@pytest.mark.asyncio
async def test_read_external_references_never_implies_confirmed(policy):
    db=FakeDB()
    result=await api.list_external_references(UUID(CASE_ID),Response(),user=USER,db=db)
    assert len(result["items"])==1
    assert result["items"][0]["provenance_status"]=="human_reported_unverified"
    assert result["agency_confirmation"] is False


def test_route_discovery_works_without_manually_editing_shared_router():
    from app.api import insights
    assert "price_preparations" in insights.MOUNTED
    paths={r.path for r in api.router.routes}
    assert "/{case_id}/references" in paths
    assert "" in paths
