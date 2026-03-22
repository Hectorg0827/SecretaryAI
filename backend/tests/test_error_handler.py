"""
Tests for utils/error_handler.py — FastAPI error handlers.
"""
import pytest

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


def _make_app():
    from app.utils.error_handler import register_error_handlers
    app = FastAPI()
    register_error_handlers(app)

    class Item(BaseModel):
        name: str
        count: int

    @app.post("/validate")
    def validate_item(item: Item):
        return item

    @app.get("/value-error")
    def raise_value():
        raise ValueError("bad value here")

    @app.get("/permission-error")
    def raise_perm():
        raise PermissionError("not allowed here")

    @app.get("/generic-error")
    def raise_generic():
        raise RuntimeError("unexpected crash")

    @app.get("/ok")
    def ok():
        return {"status": "ok"}

    return app


class TestErrorHandlers:
    def setup_method(self):
        self.client = TestClient(_make_app(), raise_server_exceptions=False)

    def test_ok_route_works(self):
        resp = self.client.get("/ok")
        assert resp.status_code == 200

    def test_validation_error_returns_422(self):
        # Send invalid payload (missing required fields)
        resp = self.client.post("/validate", json={"name": "test"})  # missing count
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "validation_error"
        assert "detail" in body
        assert body["message"] == "Request validation failed"

    def test_value_error_returns_400(self):
        resp = self.client.get("/value-error")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "bad_request"
        assert "bad value here" in body["message"]

    def test_permission_error_returns_403(self):
        resp = self.client.get("/permission-error")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "forbidden"
        assert "not allowed here" in body["message"]

    def test_generic_exception_returns_500(self):
        resp = self.client.get("/generic-error")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "internal_error"
        assert "unexpected error" in body["message"].lower()

    def test_error_responses_are_json(self):
        for path in ["/value-error", "/permission-error", "/generic-error"]:
            resp = self.client.get(path)
            assert resp.headers["content-type"].startswith("application/json")
