"""Meta Embedded Signup: independent Graph ownership checks and no token retention."""
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.intelligence.meta_embedded_signup import (
    verify_meta_coexistence_signup, MetaVerificationError,
)
from app.api.private_continuity import (
    MetaSignupFinished, finish_employee_whatsapp_signup,
)


def _make_handler(*, member="4444444444", eligible=True, missing_permission=False, wrong_app=False, wrong_waba_scope=False):
    def handler(req: httpx.Request):
        path = str(req.url.path)
        if path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "test-meta-access-token"})
        if path.endswith("/debug_token"):
            scopes = ["whatsapp_business_management", "whatsapp_business_messaging"]
            if missing_permission:
                scopes.remove("whatsapp_business_messaging")
            return httpx.Response(200, json={
                "data": {
                    "is_valid": True,
                    "app_id": "9999999999" if wrong_app else "5555555555",
                    "scopes": scopes,
                    "granular_scopes": [
                        {
                            "scope": "whatsapp_business_management",
                            "target_ids": [
                                "0000000000" if wrong_waba_scope else "3333333333"
                            ],
                        }
                    ],
                },
            })
        if path.endswith("/3333333333/phone_numbers"):
            return httpx.Response(200, json={"data": [{"id": member}]})
        if path.endswith("/4444444444"):
            return httpx.Response(200, json={
                "id": "4444444444",
                "is_on_biz_app": eligible,
                "platform_type": "CLOUD_API",
            })
        return httpx.Response(404)
    return handler


@pytest.mark.asyncio
async def test_meta_code_requires_both_waba_membership_and_phone_eligibility(monkeypatch):
    real_client = httpx.AsyncClient
    def factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(_make_handler()), **kwargs)
    with patch("app.intelligence.meta_embedded_signup.httpx.AsyncClient", side_effect=factory):
        asset = await verify_meta_coexistence_signup(
            authorization_code="test-authorization-code",
            claimed_waba_id="3333333333",
            claimed_phone_number_id="4444444444",
            meta_app_id="5555555555",
            meta_app_secret="fixture-app-secret-is-long",
        )
    assert asset.coexistence_confirmed
    assert asset.phone_number_id == "4444444444"
    assert "test-meta-access-token" not in repr(asset)


@pytest.mark.asyncio
async def test_other_waba_phone_cannot_be_bound_even_with_valid_browser_event():
    real_client = httpx.AsyncClient
    def factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(
            _make_handler(member="9999999999")
        ), **kwargs)
    with patch("app.intelligence.meta_embedded_signup.httpx.AsyncClient", side_effect=factory):
        with pytest.raises(MetaVerificationError):
            await verify_meta_coexistence_signup(
                authorization_code="test-authorization-code",
                claimed_waba_id="3333333333", claimed_phone_number_id="4444444444",
                meta_app_id="5555555555", meta_app_secret="fixture-app-secret-is-long",
            )


@pytest.mark.asyncio
async def test_business_app_coexistence_eligibility_cannot_be_assumed():
    real_client = httpx.AsyncClient
    def factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(
            _make_handler(eligible=False)
        ), **kwargs)
    with patch("app.intelligence.meta_embedded_signup.httpx.AsyncClient", side_effect=factory):
        with pytest.raises(MetaVerificationError):
            await verify_meta_coexistence_signup(
                authorization_code="test-authorization-code",
                claimed_waba_id="3333333333", claimed_phone_number_id="4444444444",
                meta_app_id="5555555555", meta_app_secret="fixture-app-secret-is-long",
            )


@pytest.mark.asyncio
async def test_employee_can_never_finalize_someone_elses_meta_signup_state(monkeypatch):
    from app.api import private_continuity
    import json
    monkeypatch.setattr(private_continuity, "_meta_embedded_signup_is_authorized", lambda: True)
    monkeypatch.setattr(
        "app.api.auth._state_pop",
        lambda _session: json.dumps({
            "kind": "lola_wa_embedded_signup",
            "company_id": "tenant-a", "user_id": "employee-a",
            "employee_consented_at": "2026-11-13T14:00:00Z",
        }),
    )
    verifier = AsyncMock()
    monkeypatch.setattr(
        "app.intelligence.meta_embedded_signup.verify_meta_coexistence_signup",
        verifier,
    )
    request = MetaSignupFinished(
        session_id="wa-this-is-an-authenticated-session-12345678",
        event="FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
        authorization_code="test-authorization-code",
        waba_id="3333333333", phone_number_id="4444444444",
    )
    with pytest.raises(HTTPException) as blocked:
        await finish_employee_whatsapp_signup(
            request,
            user={"sub": "employee-b", "company_id": "tenant-a", "role": "owner"},
            db=object(),
        )
    assert blocked.value.status_code == 403
    verifier.assert_not_awaited()



@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"missing_permission": True},
    {"wrong_app": True},
    {"wrong_waba_scope": True},
])
async def test_meta_signup_denied_without_both_permissions_correct_app_and_waba(kwargs):
    real_client = httpx.AsyncClient
    def factory(*args, **client_kwargs):
        return real_client(*args, transport=httpx.MockTransport(_make_handler(**kwargs)), **client_kwargs)
    with patch("app.intelligence.meta_embedded_signup.httpx.AsyncClient", side_effect=factory):
        with pytest.raises(MetaVerificationError):
            await verify_meta_coexistence_signup(
                authorization_code="test-authorization-code",
                claimed_waba_id="3333333333",
                claimed_phone_number_id="4444444444",
                meta_app_id="5555555555",
                meta_app_secret="fixture-app-secret-is-long",
            )
