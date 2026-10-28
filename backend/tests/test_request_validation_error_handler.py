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
            raise ValueError("Invalid email address")
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
    assert result["detail"][0]["loc"] == ["body", "email"]
    assert result["detail"][0]["type"] == "value_error"
    assert "Invalid email address" in result["detail"][0]["msg"]
    assert "secret-invalid-value" not in response.text
    assert "ctx" not in response.text
    assert "input" not in response.text
