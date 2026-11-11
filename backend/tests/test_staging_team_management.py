"""Staging-only team mutation security, without real accounts or provider writes."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import team
from app.api.deps import get_db
from app.middleware import active_session_boundary as gate6c


COMPANY = "89399925-ea7d-43f3-91cd-95f9a5636b73"
OTHER_COMPANY = "139c4adf-404e-4579-bd5f-34a40e682fbf"
OWNER = "54bde80b-3d42-4077-a0af-ed3b7384110d"
STAFF = "d60b1f3e-76b8-4132-bd68-be88151c3554"
OUTSIDER = "3a11e84a-375e-4c8c-90c0-3b607425f338"
SECOND_OWNER = "df11c30b-e04e-4e72-959d-6e85d81a7065"
ACTOR = {"sub": OWNER, "company_id": COMPANY, "role": "owner"}


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters: dict = {}
        self.change = None
        self.new_row = None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, *_args):
        return self

    def update(self, changes):
        self.change = changes
        return self

    def insert(self, row):
        self.new_row = row
        return self

    def execute(self):
        if self.db.offline:
            raise ConnectionError("synthetic database outage")
        if self.table == "audit_events":
            if self.db.audit_down:
                raise ConnectionError("synthetic audit outage")
            self.db.audits.append(self.new_row)
            return SimpleNamespace(data=[self.new_row])

        rows = [row for row in self.db.users if all(
            row.get(key) == value for key, value in self.filters.items()
        )]
        if self.change is not None:
            if self.db.race_role is not None:
                self.db.users[1]["role"] = self.db.race_role
                self.db.race_role = None
                rows = [row for row in self.db.users if all(
                    row.get(key) == value for key, value in self.filters.items()
                )]
            for row in rows:
                row.update(self.change)
        return SimpleNamespace(data=[dict(row) for row in rows])


class FakeDB:
    def __init__(self):
        self.offline = False
        self.audit_down = False
        self.race_role = None
        self.audits = []
        self.users = [
            {"id": OWNER, "company_id": COMPANY, "role": "owner", "is_active": True},
            {"id": STAFF, "company_id": COMPANY, "role": "manager", "is_active": True},
            {"id": OUTSIDER, "company_id": OTHER_COMPANY, "role": "manager", "is_active": True},
            {"id": SECOND_OWNER, "company_id": COMPANY, "role": "owner", "is_active": True},
        ]

    def table(self, table):
        assert table in {"users", "audit_events"}
        return Query(self, table)


@pytest.fixture
def staging(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(team, "_db", lambda: db)
    monkeypatch.setattr(
        team, "get_settings",
        lambda: SimpleNamespace(secretaryai_deployment_env="staging"),
    )
    return db


@pytest.mark.asyncio
async def test_owner_changes_staff_role_and_stale_role_session_is_denied(staging, monkeypatch):
    monkeypatch.setattr(gate6c, "get_db", lambda: staging)
    monkeypatch.setattr(gate6c, "is_token_revoked", lambda _jti: False)
    old_payload = {"sub": STAFF, "company_id": COMPANY, "role": "manager", "jti": "old-token"}
    assert gate6c._session_check(old_payload) is None

    result = await team.change_team_member_role(
        UUID(STAFF), team.ChangeTeamRoleRequest(role="ar_specialist"), ACTOR,
    )
    assert result == {"status": "updated", "member_id": STAFF, "role": "ar_specialist"}
    assert staging.users[1]["role"] == "ar_specialist"
    assert gate6c._session_check(old_payload)[0] == 403
    assert gate6c._session_check({**old_payload, "role": "ar_specialist"}) is None
    assert [event["event_type"] for event in staging.audits] == [
        "team_role_change_requested", "team_role_changed",
    ]
    assert staging.audits[0]["metadata"]["member_id"] == STAFF


@pytest.mark.asyncio
async def test_owner_soft_removal_blocks_existing_session(staging, monkeypatch):
    monkeypatch.setattr(gate6c, "get_db", lambda: staging)
    monkeypatch.setattr(gate6c, "is_token_revoked", lambda _jti: False)
    payload = {"sub": STAFF, "company_id": COMPANY, "role": "manager", "jti": "old-token"}
    result = await team.deactivate_team_member(UUID(STAFF), ACTOR)
    assert result == {"status": "deactivated", "member_id": STAFF}
    assert staging.users[1]["is_active"] is False
    assert gate6c._session_check(payload)[0] == 403
    assert [event["event_type"] for event in staging.audits] == [
        "team_member_deactivation_requested", "team_member_deactivated",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["role", "remove"])
async def test_only_active_current_owner_can_mutate_staff(staging, operation):
    async def call(actor):
        if operation == "role":
            return await team.change_team_member_role(
                UUID(STAFF), team.ChangeTeamRoleRequest(role="ap_specialist"), actor,
            )
        return await team.deactivate_team_member(UUID(STAFF), actor)

    for actor in ({**ACTOR, "role": "manager"}, {**ACTOR, "company_id": OTHER_COMPANY}):
        with pytest.raises(HTTPException) as exc:
            await call(actor)
        assert exc.value.status_code == 403
    staging.users[0]["role"] = "manager"
    with pytest.raises(HTTPException) as exc:
        await call(ACTOR)  # formerly owner, now stale JWT
    assert exc.value.status_code == 403
    staging.users[0]["role"] = "owner"
    staging.users[0]["is_active"] = False
    with pytest.raises(HTTPException) as exc:
        await call(ACTOR)
    assert exc.value.status_code == 403
    assert staging.users[1] == {"id": STAFF, "company_id": COMPANY, "role": "manager", "is_active": True}
    assert not staging.audits


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["role", "remove"])
async def test_cannot_change_other_company_or_company_owner(staging, operation):
    async def call(user_id):
        if operation == "role":
            return await team.change_team_member_role(
                UUID(user_id), team.ChangeTeamRoleRequest(role="ap_specialist"), ACTOR,
            )
        return await team.deactivate_team_member(UUID(user_id), ACTOR)

    for user_id, status in [(OUTSIDER, 404), (OWNER, 403), (SECOND_OWNER, 403)]:
        with pytest.raises(HTTPException) as exc:
            await call(user_id)
        assert exc.value.status_code == status
    assert all(row["is_active"] for row in staging.users)
    assert not staging.audits


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["role", "remove"])
async def test_no_write_if_audit_fails(staging, operation):
    staging.audit_down = True
    with pytest.raises(HTTPException) as exc:
        if operation == "role":
            await team.change_team_member_role(
                UUID(STAFF), team.ChangeTeamRoleRequest(role="finance_controller"), ACTOR,
            )
        else:
            await team.deactivate_team_member(UUID(STAFF), ACTOR)
    assert exc.value.status_code == 503
    assert staging.users[1]["role"] == "manager" and staging.users[1]["is_active"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["role", "remove"])
async def test_update_conflict_does_not_overwrite_concurrent_role_change(staging, operation):
    staging.race_role = "ap_specialist"
    with pytest.raises(HTTPException) as exc:
        if operation == "role":
            await team.change_team_member_role(
                UUID(STAFF), team.ChangeTeamRoleRequest(role="finance_controller"), ACTOR,
            )
        else:
            await team.deactivate_team_member(UUID(STAFF), ACTOR)
    assert exc.value.status_code == 409
    assert staging.users[1]["role"] == "ap_specialist" and staging.users[1]["is_active"]


def test_role_schema_and_actual_fastapi_routes_reject_invalid_changes(staging, monkeypatch):
    for bad_role in ("owner", "superadmin", "viewer"):
        with pytest.raises(ValidationError):
            team.ChangeTeamRoleRequest(role=bad_role)
    with pytest.raises(ValidationError):
        team.ChangeTeamRoleRequest(role="manager", company_id=OTHER_COMPANY)

    from app.middleware import subscription_guard
    monkeypatch.setattr(
        subscription_guard, "get_settings", lambda: SimpleNamespace(billing_enabled=False),
    )
    test_app = FastAPI()
    test_app.include_router(team.router, prefix="/api/team")
    test_app.dependency_overrides[team.get_current_user] = lambda: ACTOR
    test_app.dependency_overrides[get_db] = lambda: staging
    client = TestClient(test_app)
    assert client.patch(f"/api/team/members/{STAFF}/role", json={"role": "owner"}).status_code == 422
    assert client.patch("/api/team/members/not-a-uuid/role", json={"role": "manager"}).status_code == 422
    assert client.patch(f"/api/team/members/{STAFF}/role", json={"role": "ar_specialist"}).status_code == 200
    assert client.delete(f"/api/team/members/{STAFF}").status_code == 200
    assert staging.users[1]["is_active"] is False
