"""Malformed requests must fail safely without exposing submitted values."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError
from unittest.mock import patch

from app.utils.error_handler import register_error_handlers


class _Payload(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def reject_bad_email(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError(f"Invalid email address: {value}")
        return value


def test_validation_error_is_422_and_does_not_leak_input_or_exception_context():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/validate")
    async def validate(payload: _Payload):
        return {"ok": True}

    response = TestClient(app).post("/validate", json={"email": "secret-invalid-value"})

    assert response.status_code == 422
    result = response.json()
    assert result["error"] == "validation_error"
    assert result["detail"][0]["loc"] == ["body"]
    assert result["detail"][0]["type"] == "value_error"
    assert result["detail"][0]["msg"] == "Invalid request data"
    assert "secret-invalid-value" not in response.text
    assert "ctx" not in response.text
    assert "input" not in response.text


class _DictionaryPayload(BaseModel):
    seasonal_indices: dict[int, float]


def test_validation_error_does_not_leak_dynamic_dictionary_keys():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/indices")
    async def indices(payload: _DictionaryPayload):
        return {"ok": True}

    secret_key = "sensitive-customer-token"
    response = TestClient(app).post(
        "/indices", json={"seasonal_indices": {secret_key: 2.0}}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]
    assert response.json()["detail"][0]["msg"] == "Invalid request data"
    assert secret_key not in response.text


def test_validation_error_does_not_leak_numeric_dictionary_keys():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/indices")
    async def indices(payload: _DictionaryPayload):
        return {"ok": True}

    # Pydantic may coerce a numeric JSON object key to an int in error loc.
    secret_key = "99887766"
    response = TestClient(app).post(
        "/indices", json={"seasonal_indices": {secret_key: "invalid-float"}}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]
    assert secret_key not in response.text
    assert "invalid-float" not in response.text


class _CustomErrorPayload(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def reject_token(cls, value: str) -> str:
        raise PydanticCustomError("bad_token", "Invalid token: {token}", {"token": value})


def test_custom_validation_error_does_not_leak_interpolated_secrets():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/custom")
    async def custom(payload: _CustomErrorPayload):
        return {"ok": True}

    secret = "never-print-this-token"
    response = TestClient(app).post("/custom", json={"token": secret})

    assert response.status_code == 422
    assert response.json()["detail"][0] == {
        "type": "value_error", "loc": ["body"], "msg": "Invalid request data"
    }
    assert secret not in response.text
    assert "bad_token" not in response.text


def test_real_app_registers_safe_validation_handler_without_database_writes():
    # Exercise the actual app wiring and the precise staging bug route.
    # Invalid schema data must be rejected before the registration DB code.
    from app.main import app

    with patch("app.utils.rate_limiter.login_limiter.is_allowed", return_value=True):
        response = TestClient(app).post(
            "/auth/register",
            json={"company_name": "Test", "email": "sensitive-invalid-email", "password": "bad"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert "sensitive-invalid-email" not in response.text


def test_real_app_requires_auth_before_reorder_body_validation():
    from app.main import app

    response = TestClient(app).post(
        "/api/logistics/reorder/evaluate",
        json={"skus": [{"seasonal_indices": {"sensitive-tenant-key": "bad"}}]},
    )

    assert response.status_code == 401
    assert "sensitive-tenant-key" not in response.text
