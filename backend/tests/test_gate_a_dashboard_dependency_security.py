"""Gate A security: FastAPI must not load company OAuth credentials for cached viewers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dashboard
from app.api.deps import get_db
from app.auth.rbac import get_current_user


def _client(role: str, db):
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "tenant-a", "sub": "user-a", "role": role,
    }
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _snapshot():
    return {
        "payload": {
            "accounts": {
                "healthy": 1, "slowing": 0, "at_risk": 0,
                "dormant": 0, "total": 1,
            },
            "inventory_alerts": [], "sales_30d": 3.5,
            "source_quality": "partial", "source_read_status": "completed",
        },
        "generated_at": "2026-10-26T12:00:00Z",
    }


@pytest.mark.parametrize("role", ["sales_rep", "viewer", "unknown"])
def test_cached_only_roles_never_construct_provider_or_decrypt_oauth(role):
    db = MagicMock()
    with patch.object(dashboard, "get_adapter") as construct, \
         patch.object(dashboard, "read_snapshot", return_value=_snapshot()):
        with _client(role, db) as client:
            response = client.get("/api/dashboard/summary")
    assert response.status_code == 200, response.text
    assert response.json()["company_id"] == "tenant-a"
    assert response.json()["accounts"] is None
    construct.assert_not_called()
    db.table.assert_not_called()


@pytest.mark.parametrize("role", ["sales_rep", "viewer", "unknown"])
def test_missing_cached_snapshot_rejects_before_connector_construction(role):
    db = MagicMock()
    with patch.object(dashboard, "get_adapter") as construct, \
         patch.object(dashboard, "read_snapshot", return_value=None), \
         patch.object(dashboard, "compute_dashboard_payload",
                      new=AsyncMock()) as compute:
        with _client(role, db) as client:
            response = client.get("/api/dashboard/summary")
    assert response.status_code == 503
    construct.assert_not_called()
    compute.assert_not_awaited()
    db.table.assert_not_called()


def test_authorized_owner_still_builds_connector_for_live_counts():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.count = 0
    adapter = AsyncMock()
    adapter.get_emails.return_value = []
    with patch.object(dashboard, "get_adapter", return_value=adapter) as construct, \
         patch.object(dashboard, "read_snapshot", return_value=_snapshot()):
        with _client("owner", db) as client:
            response = client.get("/api/dashboard/summary")
    assert response.status_code == 200, response.text
    construct.assert_called_once()
    adapter.get_emails.assert_awaited_once_with(query="is:unread", max_results=50)
    assert response.json()["accounts"]["total"] == 1
    assert response.json()["unread_emails"] == 0


def test_authorized_but_unconfigured_provider_fails_without_false_first_load():
    db = MagicMock()
    with patch.object(dashboard, "get_adapter", side_effect=RuntimeError("fixture provider offline")), \
         patch.object(dashboard, "read_snapshot", return_value=None), \
         patch.object(dashboard, "store_snapshot") as saved:
        with _client("owner", db) as client:
            response = client.get("/api/dashboard/summary")
    assert response.status_code == 503
    assert "fixture provider offline" not in response.text
    saved.assert_not_called()
