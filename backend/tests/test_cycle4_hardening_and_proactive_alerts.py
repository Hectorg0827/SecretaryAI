"""Cycle 4 regression tests: error-leak closure, proactive alert dedup, finance chat context."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.industry_modules.loader import get_module
from app.intelligence.anomaly_detection import (
    Anomaly,
    alert_fingerprint,
    deduplicate_and_prioritize_anomalies,
    detect_inventory_anomalies,
)


def test_detect_inventory_anomalies_skips_malformed_or_missing_quantities():
    rows = [
        {"product_name": "Bad None", "total_qty": None, "weekly_sell_rate": 5},
        {"product_name": "Bad Text", "total_qty": "not-a-number", "weekly_sell_rate": 5},
        {"product_name": "Bad NaN", "total_qty": float("nan"), "weekly_sell_rate": 5},
        {"product_name": "Zero Active", "total_qty": 0, "weekly_sell_rate": "4"},
        {"product_name": "Negative", "total_qty": -3, "weekly_sell_rate": 0},
    ]
    found = detect_inventory_anomalies(rows, today=date(2026, 11, 27))
    by_entity = {item.entity_name: item.anomaly_type for item in found}
    assert by_entity == {
        "Zero Active": "zero_stock_active_item",
        "Negative": "negative_inventory",
    }


def test_proactive_alert_deduplication_cooldown_and_tenant_isolation():
    now = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    today = now.date()

    a_med = Anomaly("unusual_order_amount_high", "Acme   Beverage", "dup med", "medium", 500.0, (100.0, 400.0), today)
    a_high = Anomaly("unusual_order_amount_high", "acme beverage", "dup high", "high", 900.0, (100.0, 400.0), today)
    a_crit = Anomaly("zero_stock_active_item", "IPA 12pk", "out of stock", "critical", 0.0, (20.0, 100.0), today)
    a_recent = Anomaly("negative_inventory", "Lager Keg", "negative stock", "high", -2.0, (0.0, 100.0), today)
    a_expired = Anomaly("unusual_order_amount_low", "Delta Mart", "drop", "low", 10.0, (50.0, 200.0), today)

    fp_a = alert_fingerprint(a_high, tenant_id="tenant-1")
    fp_b = alert_fingerprint(a_high, tenant_id="tenant-2")
    assert fp_a != fp_b
    assert fp_a == alert_fingerprint(a_med, tenant_id="tenant-1")

    with pytest.raises(PermissionError):
        alert_fingerprint(a_high, tenant_id=" ")
    with pytest.raises(PermissionError):
        deduplicate_and_prioritize_anomalies([a_high], tenant_id="", now=now)
    with pytest.raises(ValueError):
        deduplicate_and_prioritize_anomalies([a_high], tenant_id="tenant-1", now=datetime(2026, 11, 27, 18, 0))

    fp_recent = alert_fingerprint(a_recent, tenant_id="tenant-1")
    fp_expired = alert_fingerprint(a_expired, tenant_id="tenant-1")
    summary = deduplicate_and_prioritize_anomalies(
        [a_med, a_high, a_crit, a_recent, a_expired],
        tenant_id="tenant-1",
        now=now,
        recent_fingerprints={
            fp_recent: now - timedelta(hours=3),
            fp_expired: now - timedelta(hours=30),
        },
        cooldown=timedelta(hours=24),
        max_alerts=2,
    )
    assert summary["deduplicated_count"] == 1
    assert summary["suppressed_count"] == 1
    assert summary["truncated_count"] == 1
    assert [a["severity"] for a in summary["active_alerts"]] == ["critical", "high"]
    assert [a["entity_name"] for a in summary["active_alerts"]] == ["IPA 12pk", "acme beverage"]


def test_wholesale_intent_categories_include_receivables_aging():
    categories = get_module("wholesale_distribution").get_intent_categories()
    assert "receivables_aging" in categories


def test_admin_health_subsystems_never_leak_raw_exception_details():
    from app.api.admin import _connector_health, _cu_health, _ingestion_health, _workflow_health

    canary = "postgresql://secret:canary-password@db.internal/prod"
    db = MagicMock()
    db.table.side_effect = RuntimeError(canary)

    for fn, args in (
        (_connector_health, (db, "co-1")),
        (_workflow_health, (db, "co-1", "2026-11-27T00:00:00Z")),
        (_cu_health, (db, "co-1", "2026-11-27T00:00:00Z")),
        (_ingestion_health, (db, "co-1", "2026-11-27T00:00:00Z")),
    ):
        res = fn(*args)
        assert res == {"error": "unavailable"}
        assert canary not in repr(res)


@pytest.mark.asyncio
async def test_agent_trigger_sync_hides_raw_upstream_exception():
    from fastapi import HTTPException
    from app.api.agent import trigger_sync

    canary = "conductor-api-token-canary-991"
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "co-1", "qbd_end_user_id": "end-u-1"}]
    )
    qbd = MagicMock()
    qbd.get_customers = AsyncMock(side_effect=RuntimeError(f"upstream rejected {canary}"))

    with patch("app.api.agent.QBDesktopAdapter", return_value=qbd):
        with pytest.raises(HTTPException) as err:
            await trigger_sync(user={"sub": "u-1", "company_id": "co-1", "role": "owner"}, db=db)

    assert err.value.status_code == 502
    assert err.value.detail == "QuickBooks sync failed. Please try again."
    assert canary not in err.value.detail


@pytest.mark.asyncio
async def test_verified_finance_chat_context_enforces_roles_and_freshness():
    from fastapi import HTTPException
    from app.api.chat import _verified_finance_chat_context

    db = MagicMock()
    owner = {"sub": "u-owner", "company_id": "co-1", "role": "owner"}
    rep = {"sub": "u-rep", "company_id": "co-1", "role": "sales_rep"}

    # Non-finance role rejected immediately.
    with pytest.raises(HTTPException) as rep_err:
        _verified_finance_chat_context(rep, db)
    assert rep_err.value.status_code == 403

    # Valid DB-verified owner gets strict non-reconciled workspace JSON.
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"id": "u-owner", "company_id": "co-1", "role": "owner", "is_active": True}]
    )
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "source_read_status": "completed",
            "source_quality": "partial",
        },
    }
    with patch("app.scheduler.dashboard_snapshot.read_snapshot", return_value=snapshot):
        raw = _verified_finance_chat_context(owner, db)

    parsed = json.loads(raw)
    assert parsed["source"] == "tenant_scoped_finance_workspace_snapshot"
    assert parsed["ledger_reconciled"] is False
    assert parsed["currency_verified"] is False
    assert "DRAFT AND WAIT" in parsed["guardrail"]
    assert set(parsed["authorized_scopes"]) == {"ap", "ar", "ledger", "pricing"}

    # Snapshot failure fails closed with 503 without leaking canary.
    canary = "secret-snapshot-db-string"
    with patch("app.scheduler.dashboard_snapshot.read_snapshot", side_effect=RuntimeError(canary)):
        with pytest.raises(HTTPException) as snap_err:
            _verified_finance_chat_context(owner, db)
    assert snap_err.value.status_code == 503
    assert canary not in snap_err.value.detail
