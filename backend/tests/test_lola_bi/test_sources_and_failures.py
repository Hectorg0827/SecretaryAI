"""Connector failures, partial results, malformed and duplicate data."""
import copy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest

from app.connectors.qb_bi_reads import QBOnlineBIReads, SourceNotSupported
from app.connectors.qb_online import QBO_OAUTH_TOKEN_URL, QBOnlineAdapter
from app.lola_bi.gateway import BusinessDataGateway, classify_error

from .synthetic import FakeAdapter, FakeReads, clock, scope_for, service


def _qbo(monkeypatch) -> QBOnlineAdapter:
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(intuit_environment="sandbox"))
    return QBOnlineAdapter("cid", "csecret", "expired-access", "revoked-refresh", "realm-1")


@pytest.mark.asyncio
async def test_failed_oauth_refresh_yields_auth_failed_not_empty(monkeypatch):
    """Real QBO client: query → 401 → refresh → 400 invalid_grant. Never an empty success."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.host}")
        if request.url.host == httpx.URL(QBO_OAUTH_TOKEN_URL).host:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(401, json={"Fault": {}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    qbo = _qbo(monkeypatch)
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=QBOnlineBIReads(qbo), clock=clock)
    snap = await gw.open_invoices()
    assert snap.status.value == "auth_failed" and snap.error == "oauth_refresh_failed"
    assert snap.records == () and not snap.usable
    assert any("oauth" in s for s in seen)
    assert all(not s.startswith(("POST sandbox", "PUT", "DELETE")) for s in seen)  # no accounting writes


@pytest.mark.asyncio
async def test_tool_reports_unavailable_with_reason_when_oauth_fails():
    err = httpx.HTTPStatusError("x", request=httpx.Request("POST", QBO_OAUTH_TOKEN_URL),
                                response=httpx.Response(400))
    svc, _ = service("owner", reads=FakeReads(fail={"bank_accounts": err}))
    result = await svc.invoke("finance_cash_position", {})
    assert result["status"] == "unavailable" and result["answer"] is None
    assert {"item": "qb.bank_accounts", "status": "auth_failed", "detail": "oauth_refresh_failed"} in \
        result["data_quality"]["missing_or_limited"]


@pytest.mark.parametrize("exc,status,code", [
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://quickbooks.api.intuit.com/v3"),
                           response=httpx.Response(403)), "auth_failed", "provider_rejected_credentials"),
    (httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://quickbooks.api.intuit.com/v3"),
                           response=httpx.Response(429)), "failed", "provider_rate_limited"),
    (httpx.ConnectTimeout("t"), "failed", "provider_unreachable"),
    (RuntimeError("QBO token refresh could not be persisted"), "auth_failed", "oauth_refresh_not_persisted"),
    (RuntimeError("No QB connector configured"), "not_configured", "quickbooks_not_connected"),
    (ValueError("QBO Balance missing"), "failed", "malformed_source_data"),
    (SourceNotSupported("x"), "not_supported", "not_supported_by_provider"),
])
def test_error_classification_is_sanitized(exc, status, code):
    got_status, got_code = classify_error(exc)
    assert (got_status.value, got_code) == (status, code)


@pytest.mark.asyncio
async def test_no_quickbooks_connector_is_not_configured():
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=None, clock=clock)
    gw._reads = None
    snap = await gw.customers()
    assert snap.status.value == "not_configured"


@pytest.mark.asyncio
async def test_partial_connector_results_keep_answer_but_flag_missing_source():
    """Collection priorities still rank AR when payments and mailbox fail, and say so."""
    adapter = FakeAdapter(email_error=RuntimeError("No email connector configured"))
    svc, _ = service("owner", reads=FakeReads(fail={"payments_between": httpx.ConnectTimeout("t")}),
                     adapter=adapter)
    result = await svc.invoke("ar_collection_priorities", {})
    assert result["status"] == "partial"
    missing = {(m["item"], m["status"]) for m in result["data_quality"]["missing_or_limited"]}
    assert ("qb.payments", "failed") in missing and ("mail.messages", "not_configured") in missing
    top = result["answer"]["priorities"][0]
    assert top["customer_name"] == "Sunrise Distributors"
    assert top["last_payment_date"] is None and top["recent_messages_from_customer"] is None
    assert not any("no payment in the last" in r for r in top["reasons"])  # unknown ≠ "no payment"


@pytest.mark.asyncio
async def test_required_source_failure_produces_no_numbers():
    svc, _ = service("owner", reads=FakeReads(fail={"open_invoices": ValueError("bad")}))
    for name in ("ar_receivables_summary", "ar_overdue_customers", "finance_cash_flow_projection"):
        result = await svc.invoke(name, {})
        assert result["status"] == "unavailable" and result["answer"] is None, name


@pytest.mark.asyncio
async def test_non_list_or_non_mapping_payloads_fail_closed():
    for bad in ({"not": "a list"}, ["row"], None):
        reads = FakeReads()

        async def broken(_bad=bad):
            return _bad
        reads.open_bills = broken
        gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=reads, clock=clock)
        snap = await gw.open_bills()
        assert snap.status.value == "failed" and snap.error == "malformed_source_data"


@pytest.mark.asyncio
async def test_identical_duplicate_records_collapse_and_conflicting_ones_block():
    data = FakeReads().data
    dup_same = data["open_invoices"] + [copy.deepcopy(data["open_invoices"][0])]
    svc, _ = service("owner", reads=FakeReads(data={"open_invoices": dup_same}))
    result = await svc.invoke("ar_receivables_summary", {})
    usd = next(a for a in result["answer"]["aging_by_currency"] if a["currency"] == "USD")
    assert usd["total_open"] == "1800.50"  # not double counted
    assert "duplicate invoice records were collapsed" in result["warnings"]

    conflict = copy.deepcopy(data["open_invoices"][0])
    conflict["balance"] = D("999.00")
    svc, _ = service("owner", reads=FakeReads(data={"open_invoices": data["open_invoices"] + [conflict]}))
    result = await svc.invoke("ar_receivables_summary", {})
    assert result["status"] == "unavailable" and result["answer"] is None


@pytest.mark.asyncio
async def test_missing_due_date_is_unaged_not_current():
    data = FakeReads().data
    rows = copy.deepcopy(data["open_invoices"])
    rows[0]["due_date"] = None
    svc, _ = service("owner", reads=FakeReads(data={"open_invoices": rows}))
    result = await svc.invoke("ar_receivables_summary", {})
    usd = next(a for a in result["answer"]["aging_by_currency"] if a["currency"] == "USD")
    assert usd["buckets"]["unaged"] == "1000.00" and usd["buckets"]["days_61_90"] == "0"
    assert usd["status"] == "partial_aging" and "missing_due_date" in usd["exceptions"]


@pytest.mark.asyncio
async def test_unknown_currency_is_reported_separately_never_merged():
    data = FakeReads().data
    rows = copy.deepcopy(data["open_invoices"])
    rows[1]["currency"] = None  # multicurrency books + no code → unknown
    svc, _ = service("owner", reads=FakeReads(data={"open_invoices": rows}))
    result = await svc.invoke("ar_receivables_summary", {})
    aging = {a["currency"]: a for a in result["answer"]["aging_by_currency"]}
    assert aging[None]["status"] == "unavailable" and aging[None]["total_open"] is None
    assert aging["USD"]["total_open"] == "1300.50"
    assert ("currency", "unverified") in {(m["item"], m["status"]) for m in
                                          result["data_quality"]["missing_or_limited"]}


@pytest.mark.asyncio
async def test_inventory_and_mailbox_are_always_labelled_partial():
    svc, _ = service("owner")
    inv = await svc._gw.inventory()
    mail = await svc._gw.messages()
    assert inv.status.value == "partial" and mail.status.value == "partial"
    assert set(mail.records[0]) == {"id", "thread_id", "from", "from_email", "subject", "date"}


@pytest.mark.asyncio
async def test_coverage_tool_reports_each_source_state():
    reads = FakeReads(fail={"profit_and_loss": SourceNotSupported("x"),
                            "open_bills": RuntimeError("No QB connector configured")})
    svc, _ = service("owner", reads=reads)
    result = await svc.invoke("bi_data_coverage", {})
    states = {s["name"]: s["state"] for s in result["answer"]["sources"]}
    assert states["profit_and_loss_month_to_date"] == "not_supported"
    assert states["open_bills"] == "not_configured"
    assert states["customers"] == "live" and states["inventory"] == "live_partial"
    assert "supplier billbacks and promotion terms" in result["answer"]["not_connected"]


@pytest.mark.asyncio
async def test_date_windows_are_inclusive_and_validated():
    reads = FakeReads()
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=reads, clock=clock)
    snap = await gw.invoices(date(2026, 9, 15), date(2026, 9, 15))
    assert [r["id"] for r in snap.records] == ["I-104"]
    with pytest.raises(ValueError):
        await gw.invoices(date(2026, 9, 16), date(2026, 9, 15))
