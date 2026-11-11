"""Manager regression: account settings and integration setup are owner-only.

No real database or external service is contacted by these tests.  A manager
must receive 403 before an endpoint can even attempt a side effect.
"""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.api import setup as setup_api
from app.api import outlook_oauth as outlook_api
from app.api.deps import get_db
from app.auth.rbac import get_current_user


class _NoDatabaseAccess:
    def table(self, _name):
        raise AssertionError("Owner-only endpoint accessed the database for a Manager")


@pytest.fixture()
def manager_client():
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api/settings")
    app.include_router(setup_api.router, prefix="/api/setup")
    app.include_router(outlook_api.router, prefix="/auth/outlook")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "test-manager", "company_id": "tenant-a", "role": "manager"
    }
    app.dependency_overrides[get_db] = lambda: _NoDatabaseAccess()
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/settings/", None),
    ("PATCH", "/api/settings/", {"timezone": "UTC"}),
    ("POST", "/api/settings/integrations/qbd", {"end_user_id": "synthetic"}),
    ("DELETE", "/api/settings/integrations/qbd", None),
    ("GET", "/auth/outlook/connect-url", None),
    ("DELETE", "/auth/outlook/disconnect", None),
    ("POST", "/api/setup/qb-desktop/start", None),
    ("GET", "/api/setup/qb-desktop/status", None),
    ("POST", "/api/setup/qb-desktop/guidance", {
        "screenshot_b64": "synthetic", "step": "test",
    }),
])
def test_manager_is_blocked_before_owner_only_api_side_effects(manager_client, method, path, body):
    response = manager_client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json()["detail"] == "Role 'manager' does not have permission: manage_users"
