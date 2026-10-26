"""Gate A: company-scoped partial briefing API, without provider calls or writes."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import briefing
from app.api.deps import get_db
from app.auth.rbac import get_current_user

NOW = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)


def snapshot(as_of=NOW):
    return {
        "generated_at": as_of.isoformat(),
        "payload": {
            "source_quality": "partial", "source_read_status": "completed",
            "accounts": {
                "healthy": 7, "slowing": 2, "at_risk": 1, "dormant": 0,
                "total": 10,
            },
            "inventory_alerts": [
                {"qb_id": "SKU-1", "stock_status": "critical"},
                {"qb_id": "SKU-2", "stock_status": "low"},
            ],
            "sales_30d": 1250.50,
        },
    }


def test_owner_projection_has_six_sections_with_evidence_and_partial_warning():
    result = briefing.build_owner_briefing(
        company_id="company-a", role="owner", snapshot=snapshot(), now=NOW,
    )
    assert result["source_coverage"] == "dashboard_snapshot_only"
    assert result["complete_digest"] is False
    assert result["source_quality"] == "partial"
    assert len(result["sections"]) == 6
    assert len(result["sections"]["red_alerts"]) == 1
    assert len(result["sections"]["yellow_alerts"]) == 1
    assert len(result["sections"]["scoreboard"]) == 1
    assert result["sections"]["opportunities"] == []
    assert result["sections"]["actions_taken"] == []
    assert "company-a:dashboard_summary" in (
        result["sections"]["scoreboard"][0]["evidence"][0]["source_record_id"]
    )
    assert result["sections"]["scoreboard"][0]["evidence"][0]["quality"] == "partial"


def test_manager_can_view_company_aggregates_not_raw_customer_or_email_data():
    result = briefing.build_owner_briefing(
        company_id="company-b", role="manager", snapshot=snapshot(), now=NOW,
    )
    text = str(result)
    assert result["audience_role"] == "manager"
    assert "email" not in text
    assert "SKU-1" not in text
    assert "SKU-2" not in text


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer", "unknown"])
def test_role_cannot_derive_aggregate_briefing(role):
    with pytest.raises(PermissionError):
        briefing.build_owner_briefing(
            company_id="company-a", role=role, snapshot=snapshot(), now=NOW,
        )


@pytest.mark.parametrize("mutation", [
    lambda s: s["payload"].update(source_read_status="failed"),
    lambda s: s["payload"].update(source_quality="unknown"),
    lambda s: s["payload"]["accounts"].update(total=999),
    lambda s: s["payload"]["accounts"].update(at_risk=-1),
    lambda s: s["payload"].update(sales_30d=float("nan")),
    lambda s: s["payload"]["inventory_alerts"][0].update(stock_status="made_up"),
])
def test_invalid_source_state_never_produces_successful_digest(mutation):
    src = snapshot()
    mutation(src)
    with pytest.raises(briefing.BriefingSourceUnavailable):
        briefing.build_owner_briefing(
            company_id="company-a", role="owner", snapshot=src, now=NOW,
        )


def test_stale_snapshots_must_be_refreshed_before_today_digest():
    old = snapshot(NOW - timedelta(days=3))
    with pytest.raises(briefing.BriefingSourceUnavailable):
        briefing.build_owner_briefing(
            company_id="company-a", role="owner", snapshot=old, now=NOW,
        )


def client_for_role(role):
    app = FastAPI()
    app.include_router(briefing.router, prefix="/api/briefing")
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "company-a", "role": role, "sub": "fixture-user",
    }
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def test_sales_rep_cannot_call_company_wide_digest_even_with_existing_snapshot():
    client = client_for_role("sales_rep")
    with patch.object(briefing, "read_snapshot") as read:
        response = client.get("/api/briefing/today")
    assert response.status_code == 403
    read.assert_not_called()


def test_owner_endpoint_reads_only_own_company_snapshot_and_no_connector():
    client = client_for_role("owner")
    snap = snapshot(datetime.now(timezone.utc))
    with patch.object(briefing, "read_snapshot", return_value=snap) as read:
        response = client.get("/api/briefing/today")
    assert response.status_code == 200
    assert response.json()["company_id"] == "company-a"
    read.assert_called_once()
    assert read.call_args.args[1:] == ("company-a", "dashboard_summary")


def test_missing_snapshot_returns_503_not_six_empty_success_cards():
    client = client_for_role("owner")
    with patch.object(briefing, "read_snapshot", return_value=None):
        response = client.get("/api/briefing/today")
    assert response.status_code == 503
