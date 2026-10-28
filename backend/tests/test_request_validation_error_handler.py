"""Malformed requests must fail safely without exposing submitted values."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

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
