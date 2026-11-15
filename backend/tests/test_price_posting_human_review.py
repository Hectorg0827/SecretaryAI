"""Synthetic-only human rule-review tests. No agency or provider calls."""
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.insights import price_posting as api

TENANT = "00000000-0000-4000-8000-000000000011"
ACTOR = "00000000-0000-4000-8000-000000000012"
RULE_ID = "00000000-0000-4000-8000-000000000013"
USER = {"company_id": TENANT, "sub": ACTOR, "role": "owner"}
RULE = {
    "id": RULE_ID, "program_key": "NJ_ECPL", "version": 1,
    "country_code": "US", "region_code": "NJ",
    "program_name": "Synthetic current price list",
    "required_details": ["brand_name"], "allowed_channels": ["retail"],
    "product_types": ["wine"], "authority_url": "https://www.nj.gov/example",
    "authority_title": "Synthetic source", "official_template_url": None,
    "applicability_note": "Human review required", "filing_deadline_rule": None,
}
REVIEW_NOTE = "I checked the official source and applicable tenant license scope for this synthetic rule."


def req(**overrides):
    data = {
        "rule_id": UUID(RULE_ID), "version": 1,
        "evidence_ref": "https://www.nj.gov/official-test-source",
        "review_note": REVIEW_NOTE,
        "checked_current_official_source": True,
        "checked_tenant_license_applicability": True,
        "acknowledged_no_filing_authority": True,
    }
    data.update(overrides)
    return api.RuleHumanReviewRequest(**data)


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.action = "select"
        self.filters = []
        self.row = None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def insert(self, row):
        self.action = "insert"
        self.row = row
        return self

    def execute(self):
        if self.table == self.db.fail_source:
            raise RuntimeError("synthetic source outage")
        if self.action == "insert":
            if self.db.fail_audit:
                raise RuntimeError("synthetic audit unavailable")
            self.db.audits.append(dict(self.row))
            return SimpleNamespace(data=[dict(self.row)])
        if self.table == "compliance_price_posting_rule_versions":
            rows = [dict(self.db.rule)]
        elif self.table == "compliance_price_posting_rule_reviews":
            rows = [dict(self.db.review)] if self.db.review else []
        else:
            raise AssertionError("unexpected table " + self.table)
        return SimpleNamespace(data=[
            r for r in rows if all(str(r.get(k)) == str(v) for k, v in self.filters)
        ])


class AtomicReview:
    def __init__(self, db, args):
        self.db, self.args = db, args

    def execute(self):
        if self.db.fail_rpc:
            raise RuntimeError("synthetic atomic review failure")
        if self.db.bad_receipt:
            return SimpleNamespace(data={"review_id": "no-audit"})
        return SimpleNamespace(data={
            "review_id": "review-synthetic",
            "audit_event_id": "audit-synthetic",
            "rule_id": RULE_ID,
            "rule_version": 1,
            "state": "documentary_review_recorded",
            "submission_permitted": False,
        })


class FakeDB:
    def __init__(self):
        self.rule = dict(RULE)
        self.review = None
        self.calls = []
        self.audits = []
        self.fail_source = None
        self.fail_audit = False
        self.fail_rpc = False
        self.bad_receipt = False

    def table(self, name):
        self.calls.append(name)
        return Query(self, name)

    def rpc(self, name, args):
        assert name == "record_price_posting_rule_review"
        self.calls.append("rpc:" + name)
        self.rpc_args = args
        return AtomicReview(self, args)


@pytest.fixture
def policy(monkeypatch):
    effect = {"value": "allow"}

    async def decide(_self, proposal):
        assert proposal.capability == "compliance.rule_review"
        assert proposal.company_id == TENANT
        assert proposal.requested_by == ACTOR
        assert proposal.action_class.value == "commit"
        return SimpleNamespace(effect=effect["value"], rule_id=None)
    monkeypatch.setattr(api.PolicyEngine, "evaluate", decide)
    return effect


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "manager"])
async def test_owner_manager_review_requires_atomic_receipt(policy, role):
    db = FakeDB()
    result = await api.record_rule_human_review(
        "NJ_ECPL", req(), user=dict(USER, role=role), db=db,
    )
    assert result["state"] == "documentary_review_recorded"
    assert result["submission_permitted"] is False
    assert result["price_publication_permitted"] is False
    assert result["regulatory_deadlines_verified_by_this_action"] is False
    assert db.rpc_args["p_company_id"] == TENANT
    assert db.rpc_args["p_reviewer_id"] == ACTOR
    assert db.rpc_args["p_policy_effect"] == "allow"
    assert db.calls.count("rpc:record_price_posting_rule_review") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["pricing_billbacks", "sales_rep", "viewer"])
async def test_nonmanager_cannot_review_even_with_view_access(policy, role):
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(), user=dict(USER, role=role), db=db)
    assert exc.value.status_code == 403
    assert db.calls == []


@pytest.mark.asyncio
async def test_review_requires_all_three_human_attestations(policy):
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review(
            "NJ_ECPL", req(checked_tenant_license_applicability=False), user=USER, db=db,
        )
    assert exc.value.status_code == 422
    assert db.calls == []


@pytest.mark.asyncio
async def test_stale_version_conflict_and_no_write(policy):
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(version=2), user=USER, db=db)
    assert exc.value.status_code == 409
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
async def test_duplicate_review_is_conflict(policy):
    db = FakeDB()
    db.review = {
        "id": "synthetic", "company_id": TENANT, "rule_id": RULE_ID,
        "reviewer_id": ACTOR, "reviewed_at": "2026-11-15T00:00:00Z",
        "evidence_ref": "trusted-synthetic-reference",
        "review_note": REVIEW_NOTE,
    }
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(), user=USER, db=db)
    assert exc.value.status_code == 409
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect,status", [("deny", 403), ("require_approval", 409)])
async def test_policy_block_is_audited_and_never_writes(policy, effect, status):
    policy["value"] = effect
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(), user=USER, db=db)
    assert exc.value.status_code == status
    assert not any(x.startswith("rpc:") for x in db.calls)
    assert len(db.audits) == 1
    assert db.audits[0]["company_id"] == TENANT
    assert db.audits[0]["policy_effect"] == effect


@pytest.mark.asyncio
async def test_unavailable_review_source_denies_write(policy):
    db = FakeDB()
    db.fail_source = "compliance_price_posting_rule_reviews"
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(), user=USER, db=db)
    assert exc.value.status_code == 503
    assert not any(x.startswith("rpc:") for x in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_rpc", "bad_receipt"])
async def test_write_or_audit_receipt_unconfirmed_is_not_success(policy, failure):
    db = FakeDB()
    setattr(db, failure, True)
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(), user=USER, db=db)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_policy_rejection_unavailable_audit_blocks_without_write(policy):
    policy["value"] = "deny"
    db = FakeDB()
    db.fail_audit = True
    with pytest.raises(HTTPException) as exc:
        await api.record_rule_human_review("NJ_ECPL", req(), user=USER, db=db)
    assert exc.value.status_code == 503
    assert not any(x.startswith("rpc:") for x in db.calls)
