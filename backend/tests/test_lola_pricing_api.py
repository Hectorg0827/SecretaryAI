"""Authenticated Lola pricing review endpoints, synthetic source rows only."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app
from pricing_auth_testkit import RoleQuery
from test_lola_pricing_bridge import Database, COMPANY, SNAP


class Db(Database):
    def __init__(self, *, current_role="owner", active=True, role_fail=False,
                 snapshots=None, costs=None, books=None, fail=None):
        super().__init__(snapshots=snapshots, costs=costs, books=books, fail=fail)
        self.role_query = RoleQuery("operator-1", COMPANY, current_role,
                                    active=active, fail=role_fail)
    def table(self, name):
        if name == "users":
            return self.role_query
        return super().table(name)


@pytest.fixture
def get_client():
    def build(*, role="owner", **db_args):
        db = Db(current_role=db_args.pop("current_role", role), **db_args)
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "operator-1", "company_id": COMPANY, "role": role,
        }
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app), db
    yield build
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_owner_sees_source_only_cost_review(get_client):
    client, db = get_client()
    response = client.get("/api/lola/chief-of-staff/pricing/snapshots/" + SNAP)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    result = response.json()
    assert result["accounting_reconciled"] is False
    assert result["price_publication_verified"] is False
    assert result["gross_margin_calculated"] is False
    assert result["recorded_cost_per_case"] == "27.150000"
    db.assert_read_only_and_scoped()


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer", "ar_specialist"])
def test_denied_roles_never_read_pricing_sources(get_client, role):
    client, db = get_client(role=role)
    assert client.get("/api/lola/chief-of-staff/pricing/snapshots").status_code == 403
    assert client.get("/api/lola/chief-of-staff/pricing/snapshots/" + SNAP).status_code == 403
    assert db.log == []


def test_pricing_billbacks_role_receives_read_only_evidence(get_client):
    client, db = get_client(role="pricing_billbacks")
    result = client.get("/api/lola/chief-of-staff/pricing/snapshots").json()
    assert result["price_changes_allowed"] is False
    assert result["items"][0]["cost_snapshot_id"] == SNAP
    db.assert_read_only_and_scoped()


@pytest.mark.parametrize("change", [{"current_role": "viewer"}, {"active": False}])
def test_stale_or_disabled_role_is_blocked(get_client, change):
    client, db = get_client(role="pricing_billbacks", **change)
    assert client.get("/api/lola/chief-of-staff/pricing/snapshots").status_code == 403
    assert db.log == []


def test_current_role_source_failure_is_not_a_permission_bypass(get_client):
    client, db = get_client(role="manager", role_fail=True)
    response = client.get("/api/lola/chief-of-staff/pricing/snapshots")
    assert response.status_code == 503
    assert "Synthetic role" not in response.text
    assert db.log == []


def test_missing_snapshot_is_404_and_not_zero_cost(get_client):
    client, _ = get_client(snapshots=[])
    assert client.get("/api/lola/chief-of-staff/pricing/snapshots/" + SNAP).status_code == 404


def test_supplier_source_outage_is_sanitized(get_client):
    client, _ = get_client(fail="pricing_cost_lines")
    response = client.get("/api/lola/chief-of-staff/pricing/snapshots/" + SNAP)
    assert response.status_code == 503
    assert "SECRET provider exception" not in response.text


def test_invalid_uuid_is_rejected(get_client):
    client, db = get_client()
    response = client.get("/api/lola/chief-of-staff/pricing/snapshots/not-a-uuid")
    assert response.status_code == 422
    assert not db.log


def test_overwide_candidate_page_is_rejected(get_client):
    client, db = get_client()
    assert client.get("/api/lola/chief-of-staff/pricing/snapshots?limit=99").status_code == 422
    assert not db.log
