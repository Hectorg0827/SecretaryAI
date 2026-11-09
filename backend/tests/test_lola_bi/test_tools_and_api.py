"""Tool registry contract and the HTTP surface (auth → scope → adapter ordering)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.api import lola_bi as api
from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.lola_bi.contracts import CONTRACT_VERSION
from app.lola_bi.scope import AssignmentScopeProvider
from app.lola_bi.tools import DEPARTMENTS, TOOLS
from app.main import app

from .synthetic import REP1, TENANT, FakeAdapter, FakeDB, FakeReads, service

ENVELOPE_KEYS = {"contract_version", "tool", "department", "tenant_id", "generated_at", "status", "answer",
                 "findings", "sources", "data_quality", "warnings", "scope", "read_only", "actions_executed"}


def test_tool_names_and_schemas_are_model_safe():
    for tool in TOOLS:
        assert re.fullmatch(r"[a-z0-9_]{1,64}", tool.name)
        definition = tool.definition()
        assert definition["input_schema"]["type"] == "object"
        assert definition["input_schema"].get("additionalProperties") is False
        assert "Read-only" in definition["description"]
        assert tool.department in DEPARTMENTS
    assert len({t.name for t in TOOLS}) == len(TOOLS)


def test_every_department_is_covered():
    assert {t.department for t in TOOLS} == set(DEPARTMENTS)


@pytest.mark.asyncio
async def test_every_tool_returns_the_versioned_read_only_envelope():
    svc, _ = service("owner")
    args = {"sales_territory_drivers": {"territory": "North"}, "crm_customer_profile": {"customer": "C1"},
            "crm_find_entity": {"query": "C1"}, "investigate_account_decline": {"customer": "C1"}}
    for tool in TOOLS:
        result = await svc.invoke(tool.name, args.get(tool.name, {}))
        assert set(result) == ENVELOPE_KEYS, tool.name
        assert result["contract_version"] == CONTRACT_VERSION
        assert result["read_only"] is True and result["actions_executed"] is False
        assert result["tenant_id"] == TENANT
        json.dumps(result)  # fully serializable; Decimals already exact strings


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("ar_overdue_customers", {"limit": 0}),
    ("ar_overdue_customers", {"company_id": "tenant-globex"}),          # cannot smuggle tenant
    ("finance_profit_and_loss", {"start": "2026-12-01"}),               # future
    ("finance_profit_and_loss", {"start": "2026-11-05", "end": "2026-11-01"}),
    ("pricing_product_margins", {"start": "2024-01-01", "end": "2026-11-01"}),  # > 400 days
    ("sales_declining_accounts", {"as_of": "2027-01-01"}),
    ("finance_cash_flow_projection", {"weeks": 14}),
    ("crm_customer_profile", {}),
    ("ap_payment_proposal", {"cash_reserve": "-5"}),
])
async def test_invalid_inputs_are_rejected_without_reading(name, args):
    svc, reads = service("owner")
    result = await svc.invoke(name, args)
    assert result["status"] == "invalid_request" and result["answer"] is None
    assert reads.calls == [] or name in ("finance_profit_and_loss", "pricing_product_margins",
                                         "sales_declining_accounts")


@pytest.mark.asyncio
async def test_unknown_tool_is_invalid():
    svc, _ = service("owner")
    assert (await svc.invoke("finance_wire_money", {}))["status"] == "invalid_request"


def test_tool_list_for_rep_excludes_company_and_cost_tools():
    svc, _ = service("sales_rep", user=REP1, accounts=frozenset({"C1"}))
    names = {t["name"] for t in svc.tool_definitions()}
    assert names == {"ar_receivables_summary", "ar_overdue_customers", "ar_upcoming_due_invoices",
                     "ar_collection_priorities", "sales_period_comparison", "sales_declining_accounts",
                     "sales_underperforming_products", "sales_projection", "crm_customer_profile",
                     "crm_find_entity", "investigate_account_decline"}


# ── HTTP ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def http(monkeypatch):
    state = {"user": {"company_id": TENANT, "sub": "owner-1", "role": "owner"}, "adapter_builds": 0}
    db = FakeDB()

    def fake_get_adapter(user, db):
        state["adapter_builds"] += 1
        return FakeAdapter(tenant=user["company_id"])

    monkeypatch.setattr(api, "get_adapter", fake_get_adapter)
    real_gateway = api.BusinessDataGateway
    monkeypatch.setattr(api, "BusinessDataGateway",
                        lambda adapter, scope, db=None: real_gateway(adapter, scope, db=db, reads=FakeReads()))
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[api.get_scope_provider] = lambda: AssignmentScopeProvider(db)
    yield TestClient(app), state
    for dep in (get_current_user, get_db, api.get_scope_provider):
        app.dependency_overrides.pop(dep, None)


def test_http_owner_can_list_and_run_tools(http):
    client, state = http
    listing = client.get("/api/lola/bi/tools")
    assert listing.status_code == 200 and len(listing.json()["tools"]) == len(TOOLS)
    assert state["adapter_builds"] == 0  # listing never touches provider credentials
    resp = client.post("/api/lola/bi/tools/ar_receivables_summary", json={})
    assert resp.status_code == 200 and resp.json()["status"] == "partial"


def test_http_viewer_is_refused_before_any_adapter_is_built(http):
    client, state = http
    state["user"] = {"company_id": TENANT, "sub": "v", "role": "viewer"}
    assert client.get("/api/lola/bi/tools").status_code == 403
    assert client.post("/api/lola/bi/tools/finance_cash_position", json={}).status_code == 403
    assert state["adapter_builds"] == 0


def test_http_rep_denied_finance_without_credentials_touched(http):
    client, state = http
    state["user"] = {"company_id": TENANT, "sub": REP1, "role": "sales_rep"}
    resp = client.post("/api/lola/bi/tools/finance_cash_position", json={})
    assert resp.status_code == 403 and state["adapter_builds"] == 0
    ok = client.post("/api/lola/bi/tools/ar_overdue_customers", json={})
    assert ok.status_code == 200
    assert "Delta" not in ok.text and ok.json()["scope"]["record_scope"] == "assigned_accounts"


def test_http_validation_and_unknown_tool(http):
    client, _ = http
    assert client.post("/api/lola/bi/tools/no_such_tool", json={}).status_code == 404
    assert client.post("/api/lola/bi/tools/finance_cash_flow_projection", json={"weeks": 99}).status_code == 422


def test_http_assignment_outage_is_503_not_company_wide(http, monkeypatch):
    client, state = http
    state["user"] = {"company_id": TENANT, "sub": REP1, "role": "sales_rep"}
    app.dependency_overrides[api.get_scope_provider] = lambda: AssignmentScopeProvider(FakeDB(fail=RuntimeError("x")))
    assert client.post("/api/lola/bi/tools/ar_overdue_customers", json={}).status_code == 503
    assert state["adapter_builds"] == 0


@pytest.mark.asyncio
async def test_real_clock_ordering_does_not_break_aging():
    """Regression: the read timestamp is later than the request timestamp in real time."""
    from app.lola_bi.gateway import BusinessDataGateway
    from app.lola_bi.tools import LolaBIService
    from .synthetic import scope_for
    from datetime import datetime, timedelta, timezone
    ticks = iter(datetime(2026, 11, 9, 23, 0, tzinfo=timezone.utc) + timedelta(milliseconds=i) for i in range(1000))
    tick = lambda: next(ticks)  # noqa: E731 — strictly increasing clock
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=FakeReads(), db=FakeDB(), clock=tick)
    result = await LolaBIService(gw, clock=tick).invoke("ar_receivables_summary", {})
    assert result["status"] == "partial" and result["answer"]["total_owed"]["by_currency"]["USD"] == "1800.50"
