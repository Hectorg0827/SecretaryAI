"""Owner-only org & access administration, and that its changes drive Lola's permissions."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lola_fakes import (
    CO_A, CO_B, EXEC_MGR, FINANCE, HR, INACTIVE, OTHER_MGR, OWNER, OWNER_B, REP1, REP2, REP3, SALES_MGR, seed,
)

from app.api.deps import get_db
from app.auth.jwt import create_access_token
from app.lola.authorization import Authorizer, build_principal
from app.lola.contracts import ResourceType
from app.main import app


def token(uid, role="owner", company=CO_A):
    return {"Authorization": "Bearer " + create_access_token({"sub": uid, "company_id": company, "role": role})}


@pytest.fixture
def db():
    d = seed()
    app.dependency_overrides[get_db] = lambda: d
    yield d
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def put(client, uid, body, as_uid=OWNER, role="owner"):
    return client.put(f"/api/lola/org/{uid}", json=body, headers=token(as_uid, role))


def visible(db, uid):
    a = Authorizer(build_principal(db, CO_A, uid), db)
    return sorted(k for k in ("QB-1", "QB-2", "QB-3", "QB-4") if a.can_see_account(k))


class TestAccess:
    def test_owner_sees_everyone_with_profiles(self, db, client):
        r = client.get("/api/lola/org", headers=token(OWNER))
        assert r.status_code == 200
        body = r.json()
        rep = next(m for m in body["members"] if m["user_id"] == REP1)
        assert rep["profile"]["territories"] == ["West"] and rep["reports_to_name"] == "Sam Sales-Manager"
        assert rep["allowed_levels"] == ["employee"]
        assert "department_manager" in next(m for m in body["members"] if m["user_id"] == SALES_MGR)["allowed_levels"]
        assert "hr" in body["departments"]

    @pytest.mark.parametrize("uid,role", [(SALES_MGR, "manager"), (REP1, "owner"), (FINANCE, "back_office")])
    def test_non_owners_refused_even_with_forged_token(self, db, client, uid, role):
        assert client.get("/api/lola/org", headers=token(uid, role)).status_code == 403
        assert put(client, REP2, {"org_level": "employee"}, as_uid=uid, role=role).status_code == 403

    def test_inactive_owner_refused(self, db, client):
        db.rows("users", id=OWNER)[0]["is_active"] = False
        assert client.get("/api/lola/org", headers=token(OWNER)).status_code == 403

    def test_other_company_members_invisible_and_untouchable(self, db, client):
        r = client.get("/api/lola/org", headers=token(OWNER_B, company=CO_B)).json()
        assert {m["user_id"] for m in r["members"]} == {OWNER_B}
        # A token claiming company A for B's owner fails the database membership check.
        assert put(client, REP1, {"org_level": "employee"}, as_uid=OWNER_B).status_code == 403
        assert client.put(f"/api/lola/org/{REP1}", json={"org_level": "employee"},
                          headers=token(OWNER_B, company=CO_B)).status_code == 404

    def test_outage_is_503(self, db, client):
        db.fail.add("lola_org_members")
        assert client.get("/api/lola/org", headers=token(OWNER)).status_code == 503


class TestValidation:
    def test_cannot_lift_above_role(self, db, client):
        r = put(client, REP1, {"org_level": "executive", "department": "sales"})
        assert r.status_code == 422 and "change their role" in r.json()["detail"]
        assert put(client, OWNER, {"org_level": "employee"}).status_code == 422

    def test_department_manager_needs_department(self, db, client):
        assert put(client, EXEC_MGR, {"org_level": "department_manager", "department": "general"}).status_code == 422

    def test_reports_to_rules(self, db, client):
        assert put(client, REP1, {"manager_user_id": REP1}).status_code == 422
        assert put(client, REP1, {"manager_user_id": INACTIVE}).status_code == 422
        assert put(client, REP1, {"manager_user_id": OWNER_B}).status_code == 422
        # SALES_MGR reports to OWNER; making OWNER report to REP1 (who reports to SALES_MGR) loops.
        assert put(client, OWNER, {"org_level": "executive", "manager_user_id": REP1}).status_code == 422

    def test_bad_inputs(self, db, client):
        assert put(client, REP1, {"action_limit": "-5"}).status_code == 422
        assert put(client, REP1, {"territories": ["x" * 61]}).status_code == 422
        assert put(client, REP1, {"company_id": CO_B}).status_code == 422
        assert put(client, "nobody", {"org_level": "employee"}).status_code == 404

    def test_territories_are_cleaned(self, db, client):
        r = put(client, REP2, {"territories": [" North ", "north", "", "South  East"]})
        assert r.json()["profile"]["territories"] == ["North", "South East"]


class TestEffect:
    def test_territory_change_takes_effect_immediately(self, db, client):
        assert visible(db, REP2) == ["QB-2"]
        assert put(client, REP2, {"department": "sales", "manager_user_id": SALES_MGR,
                                  "territories": ["West"]}).status_code == 200
        assert visible(db, REP2) == ["QB-1", "QB-2", "QB-4"]

    def test_moving_a_rep_moves_their_accounts_between_managers(self, db, client):
        assert "QB-3" not in visible(db, SALES_MGR)
        put(client, REP3, {"department": "sales", "manager_user_id": SALES_MGR})
        assert "QB-3" in visible(db, SALES_MGR) and "QB-3" not in visible(db, OTHER_MGR)

    def test_finance_clearance_grants_and_removal_revokes(self, db, client):
        put(client, REP1, {"department": "sales", "manager_user_id": SALES_MGR, "territories": ["West"],
                           "financial_clearance": True})
        assert Authorizer(build_principal(db, CO_A, REP1), db).can(ResourceType.RECEIVABLES).allowed
        put(client, REP1, {"department": "sales", "manager_user_id": SALES_MGR, "territories": ["West"]})
        assert not Authorizer(build_principal(db, CO_A, REP1), db).can(ResourceType.RECEIVABLES).allowed

    def test_hr_clearance_removed(self, db, client):
        put(client, HR, {"department": "hr", "hr_clearance": False})
        assert not Authorizer(build_principal(db, CO_A, HR), db).can(ResourceType.HR).allowed

    def test_every_change_is_audited_with_before_and_after(self, db, client):
        put(client, REP1, {"department": "sales", "manager_user_id": SALES_MGR, "territories": ["East"]})
        ev = [e for e in db.tables["audit_events"] if e.get("event_type") == "lola_org_profile_updated"]
        assert ev and ev[-1]["actor_id"] == OWNER
        assert ev[-1]["before_snapshot"]["territories"] == ["West"]
        assert ev[-1]["after_snapshot"]["territories"] == ["East"]


class TestReset:
    def test_reset_returns_to_role_defaults(self, db, client):
        assert visible(db, EXEC_MGR) == ["QB-1", "QB-2", "QB-3", "QB-4"]
        put(client, EXEC_MGR, {"org_level": "department_manager", "department": "finance"})
        assert visible(db, EXEC_MGR) == []
        r = client.delete(f"/api/lola/org/{EXEC_MGR}", headers=token(OWNER))
        assert r.status_code == 200 and visible(db, EXEC_MGR) == ["QB-1", "QB-2", "QB-3", "QB-4"]

    def test_cannot_remove_someone_with_reports(self, db, client):
        assert client.delete(f"/api/lola/org/{SALES_MGR}", headers=token(OWNER)).status_code == 409
