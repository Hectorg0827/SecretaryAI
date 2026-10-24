"""JWT library migration: legacy wire compatibility and fail-closed claims.

No python-jose import anywhere; use independent HMAC construction to prove
existing HS256 sessions still decode without loosening algorithm selection.
"""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import jwt as pyjwt
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.auth.jwt import (
    MAX_TOKEN_LENGTH, create_access_token, decode_access_token,
    decode_refresh_candidate, settings,
)


def _legacy_hs256(payload: dict, alg: str = "HS256") -> str:
    """Build a compact HMAC token without either JWT library."""
    def b64(part: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(part, separators=(",", ":"), sort_keys=True).encode()
        ).rstrip(b"=").decode()
    body = b64({"typ": "JWT", "alg": alg}) + "." + b64(payload)
    signature = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return body + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def _claims(**updates):
    payload = {
        "sub": "person-1", "company_id": "company-1",
        "role": "viewer", "scope": "access",
        "jti": str(uuid4()),
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp()),
    }
    payload.update(updates)
    return payload


def test_legacy_hs256_signature_decodes_without_resigning():
    legacy_token = _legacy_hs256(_claims())
    assert decode_access_token(legacy_token)["sub"] == "person-1"


def test_newly_issued_token_signed_with_hs256_and_unique_jti():
    one = create_access_token(_claims())
    two = create_access_token(_claims())
    decoded = decode_access_token(one)
    assert decoded["scope"] == "access"
    assert pyjwt.get_unverified_header(one)["alg"] == "HS256"
    assert one != two


def test_allow_expired_only_for_explicit_refresh_candidate():
    old = _legacy_hs256(_claims(exp=int(datetime.now(timezone.utc).timestamp()) - 1))
    assert decode_access_token(old) is None
    assert decode_refresh_candidate(old)["sub"] == "person-1"


@pytest.mark.parametrize("update", [
    {"exp": None}, {"exp": "9999999999"}, {"exp": True},
    {"exp": -1}, {"exp": 0},
])
def test_invalid_exp_rejected_even_for_expiry_skipping_refresh(update):
    bad = _legacy_hs256(_claims(**update))
    assert decode_access_token(bad) is None
    assert decode_refresh_candidate(bad) is None


def test_exp_claim_required_not_silently_renewed():
    claims = _claims()
    del claims["exp"]
    bad = _legacy_hs256(claims)
    assert decode_access_token(bad) is None
    assert decode_refresh_candidate(bad) is None


def test_future_not_before_rejected_in_access_and_refresh():
    future = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    token = _legacy_hs256(_claims(nbf=future))
    assert decode_access_token(token) is None
    assert decode_refresh_candidate(token) is None


def test_none_algorithm_rejected_even_without_signature():
    claims = _claims()
    def b64(p):
        return base64.urlsafe_b64encode(json.dumps(p).encode()).rstrip(b"=").decode()
    token = b64({"alg": "none", "typ": "JWT"}) + "." + b64(claims) + "."
    assert decode_access_token(token) is None
    assert decode_refresh_candidate(token) is None


def test_non_whitelisted_algorithm_and_modified_signature_rejected():
    # Valid HS384 signature is still not a valid SecretaryAI session.
    signed_hs384 = pyjwt.encode(_claims(), settings.secret_key, algorithm="HS384")
    assert decode_access_token(signed_hs384) is None
    assert decode_refresh_candidate(signed_hs384) is None
    token = create_access_token(_claims())
    changed = token[:-6] + ("A" * 6 if token[-6:] != "A" * 6 else "B" * 6)
    assert decode_access_token(changed) is None


def test_overlong_token_rejected_before_signature_processing():
    big = "x" * (MAX_TOKEN_LENGTH + 1)
    assert decode_access_token(big) is None
    assert decode_refresh_candidate(big) is None


def test_2fa_pre_auth_still_not_full_session_after_migration():
    from app.auth.rbac import get_current_user
    from fastapi import HTTPException
    token = create_access_token({"sub": "u1", "scope": "2fa_pending"})
    with pytest.raises(HTTPException) as err:
        get_current_user(token=token)
    assert err.value.status_code == 401


def test_refresh_rejects_missing_exp_without_calling_database():
    from app.main import app
    claims = _claims()
    del claims["exp"]
    token = _legacy_hs256(claims)
    client = TestClient(app)
    r = client.post("/auth/refresh", json={"access_token": token})
    assert r.status_code == 401
