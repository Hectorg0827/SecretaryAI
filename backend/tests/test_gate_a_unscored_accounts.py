"""Gate A: one unscorable account is counted as unknown, never as $0 or an outage.

The data-finance contract rejects credit, missing and malformed balances in
score_account. The snapshot must keep scoring the other accounts, and both
cached-snapshot readers must accept the unknown bucket in the total.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api import briefing
from app.scheduler.dashboard_snapshot import (
    SnapshotDataInvalid,
    compute_dashboard_payload,
    validate_dashboard_cached_payload,
)

NOW = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)
SCORED = ("healthy", "slowing", "at_risk", "dormant")


class Reader:
    def __init__(self, customers):
        self.customers = customers

    async def get_all_customers(self):
        return self.customers

    async def get_orders_last_n_days(self, days):
        return []

    async def get_inventory_merged(self):
        return []


@pytest.mark.asyncio
async def test_unobserved_or_malformed_balances_are_unknown_not_zero():
    payload = await compute_dashboard_payload(Reader([
        SimpleNamespace(qb_id="ok", name="Ok", balance=Decimal("0")),
        SimpleNamespace(qb_id="missing", name="Missing"),
        {"qb_id": "null", "name": "Null", "balance": None},
        {"qb_id": "text", "name": "Text", "balance": "N/A"},
        {"qb_id": "nan", "name": "NaN", "balance": Decimal("NaN")},
    ]))
    accounts = payload["accounts"]

    assert accounts["unknown"] == 4
    assert accounts["total"] == 5
    assert sum(accounts[k] for k in SCORED) == 1
    validate_dashboard_cached_payload(payload)


@pytest.mark.asyncio
async def test_account_rejected_by_scoring_contract_does_not_void_others(monkeypatch):
    from app.intelligence import account_health

    real_score = account_health.score_account

    def score(**kwargs):
        if kwargs["current_balance"] < 0:
            raise ValueError("current_balance is missing or invalid")
        return real_score(**kwargs)

    monkeypatch.setattr(account_health, "score_account", score)
    payload = await compute_dashboard_payload(Reader([
        SimpleNamespace(qb_id="credit", name="Credit", balance=Decimal("-12.50")),
        SimpleNamespace(qb_id="ordinary", name="Ordinary", balance=Decimal("0")),
    ]))
    accounts = payload["accounts"]

    assert accounts["unknown"] == 1
    assert accounts["total"] == 2
    assert sum(accounts[k] for k in SCORED) == 1
    validate_dashboard_cached_payload(payload)


def _payload(**accounts):
    return {
        "source_quality": "partial", "source_read_status": "completed",
        "accounts": accounts, "inventory_alerts": [], "sales_30d": 10.0,
    }


def test_cached_payload_without_unknown_bucket_still_validates():
    validate_dashboard_cached_payload(
        _payload(healthy=1, slowing=0, at_risk=0, dormant=0, total=1)
    )


@pytest.mark.parametrize("accounts", [
    dict(healthy=1, slowing=0, at_risk=0, dormant=0, unknown=1, total=1),
    dict(healthy=1, slowing=0, at_risk=0, dormant=0, unknown=-1, total=0),
    dict(healthy=1, slowing=0, at_risk=0, dormant=0, unknown="1", total=2),
])
def test_cached_payload_rejects_inconsistent_or_invalid_unknown(accounts):
    with pytest.raises(SnapshotDataInvalid):
        validate_dashboard_cached_payload(_payload(**accounts))


def _snapshot(**accounts):
    return {"generated_at": NOW.isoformat(), "payload": _payload(**accounts)}


def test_briefing_stays_available_and_reports_unscored_accounts():
    result = briefing.build_owner_briefing(
        company_id="company-a", role="owner", now=NOW,
        snapshot=_snapshot(healthy=7, slowing=0, at_risk=1, dormant=0, unknown=2, total=10),
    )
    title = result["sections"]["scoreboard"][0]["title"]
    assert "8 classified accounts and 2 unscored accounts" in title


def test_briefing_rejects_unknown_bucket_that_breaks_the_total():
    with pytest.raises(briefing.BriefingSourceUnavailable):
        briefing.build_owner_briefing(
            company_id="company-a", role="owner", now=NOW,
            snapshot=_snapshot(healthy=7, slowing=0, at_risk=1, dormant=0, unknown=2, total=8),
        )
