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
    assert result["detail"][0]["loc"] == ["body", "email"]
    assert result["detail"][0]["type"] == "value_error"
    assert result["detail"][0]["msg"] == "Invalid value"
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
    assert response.json()["detail"][0]["loc"] == ["body", "seasonal_indices", "<redacted_key>"]
    assert response.json()["detail"][0]["msg"] == "Input should be a valid integer"
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
    assert response.json()["detail"][0]["loc"] == ["body", "seasonal_indices", "<redacted_key>"]
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
        "type": "value_error", "loc": ["body", "token"], "msg": "Invalid value"
    }
    assert secret not in response.text
    assert "bad_token" not in response.text


class _SKUData(BaseModel):
    supplier_name: str


class _NestedReorderPayload(BaseModel):
    skus: list[_SKUData]


def test_nested_model_fields_remain_attributable_without_reflecting_input():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/reorder")
    async def reorder(payload: _NestedReorderPayload):
        return {"ok": True}

    response = TestClient(app).post("/reorder", json={"skus": [{"supplier_name": 123}]})
    assert response.status_code == 422
    assert response.json()["detail"][0] == {
        "type": "string_type",
        "loc": ["body", "skus", 0, "supplier_name"],
        "msg": "Input should be a valid string",
    }
    assert "input" not in response.text


def test_real_reorder_schema_preserves_fields_and_redacts_union_dictionary_keys():
    from app.api.logistics import ReorderQueueRequest

    app = FastAPI()
    register_error_handlers(app)

    @app.post("/reorder")
    async def reorder(payload: ReorderQueueRequest):
        return {"ok": True}

    sku = {
        "sku": "SKU-1", "name": "Item", "supplier_id": "supplier-1",
        "daily_sales": [1.0], "lead_time_days": [2.0],
    }
    bad_name = TestClient(app).post("/reorder", json={"skus": [{**sku, "supplier_name": 55}]})
    assert bad_name.status_code == 422
    assert bad_name.json()["detail"][0]["loc"] == ["body", "skus", 0, "supplier_name"]

    secret_key = "99887766"
    bad_indices = TestClient(app).post("/reorder", json={
        "skus": [{**sku, "seasonal_indices": {secret_key: "invalid-float"}}]
    })
    assert bad_indices.status_code == 422
    assert secret_key not in bad_indices.text
    assert "invalid-float" not in bad_indices.text
    assert any(
        "<redacted_key>" in item["loc"] for item in bad_indices.json()["detail"]
    )


class _UnlistedDictionaryPayload(BaseModel):
    arbitrary_flags: dict[str, int]


def test_unknown_dictionary_field_name_is_protected_using_schema():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/flags")
    async def flags(payload: _UnlistedDictionaryPayload):
        return {"ok": True}

    secret_key = "customer-secret-as-key"
    response = TestClient(app).post(
        "/flags", json={"arbitrary_flags": {secret_key: "bad"}}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == [
        "body", "arbitrary_flags", "<redacted_key>"
    ]
    assert secret_key not in response.text


def test_reorder_domain_422_messages_are_preserved_and_nan_is_rejected():
    from app.api.logistics import _weekly_seasonal_indices

    app = FastAPI()
    register_error_handlers(app)

    @app.post("/calendar")
    async def calendar():
        _weekly_seasonal_indices({1: 1.0})
        return {"ok": True}

    @app.post("/non-finite")
    async def non_finite():
        _weekly_seasonal_indices([float("nan")] * 52)
        return {"ok": True}

    response = TestClient(app).post("/calendar")
    assert response.status_code == 422
    assert response.json()["detail"] == "Seasonal indices must cover exactly 52 weeks"

    non_finite = TestClient(app).post("/non-finite")
    assert non_finite.status_code == 422
    assert non_finite.json()["detail"] == "Seasonal indices must be 52 finite nonnegative multipliers"


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


def test_internal_pydantic_validation_errors_fail_closed_without_leaking_secrets():
    # Direct model validation inside a route is not an HTTP body validation
    # error. Pydantic's exception subclasses ValueError and includes input_value
    # in its string representation, so the generic ValueError handler is unsafe.
    app = FastAPI()
    register_error_handlers(app)

    class _InternalModel(BaseModel):
        amount: int

    @app.get("/internal")
    async def internal():
        _InternalModel.model_validate({"amount": "private-source-token"})
        return {"ok": True}

    response = TestClient(app, raise_server_exceptions=False).get("/internal")

    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert "private-source-token" not in response.text
    assert "input_value" not in response.text
